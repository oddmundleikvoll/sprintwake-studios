"""Room blocking and camera coverage, independent of media generation.

Coordinates are metres: X east, Y north, Z up. The axis is A -> B;
side +1 means the left half-plane of that directed line. Camera poses,
projection, warnings and storyboard descriptions use this one convention.
"""
from __future__ import annotations

import copy
from contextlib import contextmanager
import fcntl
import hashlib
import json
import math

from workspace import _atomic_text, new_shot


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def number(value, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"Verdien må være mellom {low} og {high}.")
    return value


def actors_by_id(plan):
    return {a["id"]: a for a in plan["actors"]}


def validate(plan):
    if not isinstance(plan, dict) or set(plan) != {"title", "locationId", "width", "depth", "height", "aspect", "axis", "side", "actors", "cameras"}:
        raise ValueError("Ugyldig romkart.")
    for key in ("title", "locationId"):
        if not isinstance(plan[key], str) or len(plan[key]) > 300:
            raise ValueError("Ugyldig romnavn eller sted.")
    number(plan["width"], 2, 40); number(plan["depth"], 2, 40)
    number(plan["height"], 2, 8); number(plan["aspect"], .5, 2)
    if type(plan["side"]) is not int or plan["side"] not in (-1, 1):
        raise ValueError("Velg en side av handlingsaksen.")
    if not isinstance(plan["actors"], list) or len(plan["actors"]) > 8:
        raise ValueError("Romkartet støtter opptil åtte figurer.")
    for actor in plan["actors"]:
        if not isinstance(actor, dict) or set(actor) != {"id", "characterId", "name", "x", "y", "height", "lookAt"}:
            raise ValueError("Ugyldig figurplassering.")
        for key in ("id", "characterId", "name", "lookAt"):
            if not isinstance(actor[key], str) or len(actor[key]) > 300:
                raise ValueError("Ugyldig figurreferanse.")
        if not actor["id"]:
            raise ValueError("Figuren mangler ID.")
        number(actor["x"], .1, plan["width"] - .1); number(actor["y"], .1, plan["depth"] - .1)
        number(actor["height"], .5, min(2.5, plan["height"]))
    actors = actors_by_id(plan)
    if len(actors) != len(plan["actors"]):
        raise ValueError("Duplisert figur-ID.")
    if any(a["lookAt"] and (a["lookAt"] not in actors or a["lookAt"] == a["id"]) for a in actors.values()):
        raise ValueError("Figurens blikk peker på en ukjent figur.")
    axis = plan["axis"]
    if not isinstance(axis, list) or (len(actors) >= 2 and (len(axis) != 2 or len(set(axis)) != 2)) or any(i not in actors for i in axis):
        raise ValueError("Velg to forskjellige figurer som handlingsakse.")
    if not isinstance(plan["cameras"], list) or len(plan["cameras"]) > 12:
        raise ValueError("Romkartet støtter opptil tolv kameraer.")
    camera_ids = set()
    for camera in plan["cameras"]:
        if not isinstance(camera, dict) or set(camera) != {"id", "name", "kind", "x", "y", "z", "lens", "aimIds", "aim", "selected"}:
            raise ValueError("Ugyldig kamera.")
        if not all(isinstance(camera[k], str) and 0 < len(camera[k]) <= 300 for k in ("id", "name", "kind")) or camera["id"] in camera_ids:
            raise ValueError("Ugyldig kamera-ID eller navn.")
        camera_ids.add(camera["id"])
        number(camera["x"], .1, plan["width"] - .1); number(camera["y"], .1, plan["depth"] - .1)
        number(camera["z"], .3, plan["height"] - .1); number(camera["lens"], 18, 135)
        if not isinstance(camera["aimIds"], list) or len(set(camera["aimIds"])) != len(camera["aimIds"]) or any(i not in actors for i in camera["aimIds"]):
            raise ValueError("Kameraet sikter mot en ukjent figur.")
        if not isinstance(camera["aim"], list) or len(camera["aim"]) != 3:
            raise ValueError("Ugyldig kamerasikte.")
        for value, high in zip(camera["aim"], (plan["width"], plan["depth"], plan["height"])):
            number(value, 0, high)
        if type(camera["selected"]) is not bool:
            raise ValueError("Ugyldig kameravalg.")
    return plan


def new_plan(board, location_id=""):
    location = next((x for x in (board.get("filmBible") or {}).get("locations", []) if x.get("id") == location_id), {})
    cast = board.get("characters", [])[:8]
    actors = []
    for i, character in enumerate(cast or [{"id": "", "name": "Figur A"}, {"id": "", "name": "Figur B"}]):
        actors.append({"id": f"actor-{i+1}", "characterId": character["id"], "name": character.get("name") or f"Figur {i+1}",
                       "x": 3 + 2 * i if i < 2 else 1 + (i-2) % 6, "y": 3 if i < 2 else 4.6,
                       "height": 1.7, "lookAt": ""})
    axis = [a["id"] for a in actors[:2]] if len(actors) > 1 else []
    if axis:
        actors[0]["lookAt"], actors[1]["lookAt"] = axis[1], axis[0]
    return {"title": location.get("title") or location.get("name") or "Nytt rom", "locationId": location_id,
            "width": 8, "depth": 6, "height": 2.8, "aspect": 16/9, "axis": axis,
            "side": 1, "actors": actors, "cameras": []}


def axis_basis(plan):
    if len(plan["axis"]) != 2:
        return (1., 0.), (0., float(plan["side"]))
    actors = actors_by_id(plan)
    a, b = (actors[i] for i in plan["axis"])
    dx, dy = b["x"] - a["x"], b["y"] - a["y"]
    length = math.hypot(dx, dy)
    if length < .4:
        raise ValueError("Flytt de to aksefigurene minst 40 cm fra hverandre.")
    return (dx / length, dy / length), (-dy / length * plan["side"], dx / length * plan["side"])


def side_distance(plan, x, y):
    if len(plan["axis"]) != 2:
        return 1.
    a = actors_by_id(plan)[plan["axis"][0]]
    _, normal = axis_basis(plan)
    return (x - a["x"]) * normal[0] + (y - a["y"]) * normal[1]


def target(plan, camera):
    actors = actors_by_id(plan)
    chosen = [actors[i] for i in camera["aimIds"]]
    if not chosen:
        return camera["aim"]
    fraction = .5 if camera["kind"] == "master" else .91
    return [sum(a[k] for a in chosen)/len(chosen) for k in ("x", "y")] + [sum(a["height"] * fraction for a in chosen)/len(chosen)]


def camera_basis(plan, camera):
    aim = target(plan, camera)
    direction = [aim[i] - camera[k] for i, k in enumerate(("x", "y", "z"))]
    length = math.sqrt(sum(v*v for v in direction))
    horizontal = math.hypot(direction[0], direction[1])
    if length < .1 or horizontal < .05:
        raise ValueError("Kameraets sikte må ligge foran kameraet.")
    forward = [v/length for v in direction]
    right = [direction[1]/horizontal, -direction[0]/horizontal, 0]
    up = [right[1]*forward[2], -right[0]*forward[2], right[0]*forward[1]-right[1]*forward[0]]
    return forward, right, up


def project_point(plan, camera, point):
    forward, right, up = camera_basis(plan, camera)
    offset = [point[i] - camera[k] for i, k in enumerate(("x", "y", "z"))]
    depth = sum(a*b for a, b in zip(offset, forward))
    if depth <= .05:
        return None
    sw = 36 if plan["aspect"] >= 1 else 36 * plan["aspect"]
    sh = sw / plan["aspect"]
    return (.5 + sum(a*b for a,b in zip(offset,right))*camera["lens"]/sw/depth,
            .5 - sum(a*b for a,b in zip(offset,up))*camera["lens"]/sh/depth, depth)


def actor_in_frame(plan, camera, actor):
    _, right, _ = camera_basis(plan, camera)
    points = [project_point(plan,camera,(actor["x"]+right[0]*x,actor["y"]+right[1]*x,z))
              for x in (-.25,.25) for z in (0, actor["height"])]
    points = [p for p in points if p]
    return bool(points and min(p[0] for p in points) < 1 and max(p[0] for p in points) > 0
                and min(p[1] for p in points) < 1 and max(p[1] for p in points) > 0)


def warnings(plan, camera):
    issues = []
    try:
        if side_distance(plan,camera["x"],camera["y"]) < .08:
            issues.append("Kameraet krysser eller ligger på handlingsaksen.")
        camera_basis(plan,camera)
        for actor in plan["actors"]:
            if math.hypot(actor["x"]-camera["x"],actor["y"]-camera["y"]) < .35:
                issues.append("Kameraet står for nær " + actor["name"] + ".")
        for aid in camera["aimIds"]:
            a = actors_by_id(plan)[aid]
            point = project_point(plan,camera,(a["x"],a["y"],a["height"]*.91))
            if not point or not .03 < point[0] < .97 or not .03 < point[1] < .97:
                issues.append(a["name"] + " ligger utenfor utsnittet.")
            if camera["kind"] == "master":
                foot = project_point(plan,camera,(a["x"],a["y"],0))
                head = project_point(plan,camera,(a["x"],a["y"],a["height"]))
                if any(not p or not .02 < p[0] < .98 or not .02 < p[1] < .98 for p in (foot,head)):
                    issues.append("Totalbildet kutter " + a["name"] + ". Flytt kameraet bakover eller bruk videre brennvidde.")
    except ValueError as error:
        issues.append(str(error))
    return issues


def propose(plan):
    validate(plan)
    if not plan["actors"]:
        raise ValueError("Plasser minst én figur først.")
    along, normal = axis_basis(plan)
    actors = actors_by_id(plan)
    pair = [actors[i] for i in plan["axis"]] if plan["axis"] else plan["actors"][:1]
    mid = [sum(a[k] for a in pair)/len(pair) for k in ("x","y")]
    span = math.hypot(pair[-1]["x"]-pair[0]["x"],pair[-1]["y"]-pair[0]["y"])
    specs = [("master", "Totalbilde", mid, 0, max(2.5, span*1.4), 28, [a["id"] for a in plan["actors"]])]
    if len(pair) == 2:
        a,b = pair
        specs += [("ots-a", "Motskudd mot " + b["name"], [a["x"],a["y"]], -.8, .5, 35, [b["id"]]),
                  ("ots-b", "Motskudd mot " + a["name"], [b["x"],b["y"]], .8, .5, 35, [a["id"]])]
    for i,a in enumerate(pair):
        specs.append(("close-"+a["id"], "Nærbilde · "+a["name"], [a["x"],a["y"]], .65 if i==0 else -.65, 1.5, 65, [a["id"]]))
    result = []
    for cid,name,base,dx,dy,lens,aim_ids in specs:
        desired = (base[0]+along[0]*dx+normal[0]*dy, base[1]+along[1]*dx+normal[1]*dy)
        candidates = [desired] + [( .2+(plan["width"]-.4)*i/24, .2+(plan["depth"]-.4)*j/24) for i in range(25) for j in range(25)]
        possible = [p for p in candidates if .15<=p[0]<=plan["width"]-.15 and .15<=p[1]<=plan["depth"]-.15
                    and side_distance(plan,*p) >= .15 and all(math.hypot(p[0]-a["x"],p[1]-a["y"])>.4 for a in plan["actors"])]
        if not possible:
            raise ValueError("Det er ikke plass til kameraer på valgt side. Flytt figurene eller bytt side.")
        positions=sorted(possible,key=lambda p:(p[0]-desired[0])**2+(p[1]-desired[1])**2)
        camera={"id":cid,"name":name,"kind":"master" if cid=="master" else "ots" if cid.startswith("ots") else "close",
                "x":positions[0][0],"y":positions[0][1],"z":min(1.55,plan["height"]-.2),"lens":lens,
                "aimIds":aim_ids,"aim":[*mid,1.55],"selected":True}
        usable=False
        for focal in ([28,24,21,18] if cid=="master" else [lens]):
            for x,y in positions:
                camera.update(x=round(x,3),y=round(y,3),lens=focal)
                if not warnings(plan,camera):
                    usable=True
                    break
            if usable: break
        if not usable:
            camera.update(x=round(positions[0][0],3),y=round(positions[0][1],3),lens=lens,selected=False)
        result.append(camera)
    return result


def camera_text(plan, camera):
    aim = target(plan,camera)
    forward,_,_ = camera_basis(plan,camera)
    bearing = (math.degrees(math.atan2(forward[0],forward[1]))+360)%360
    direction = ["north","north-east","east","south-east","south","south-west","west","north-west"][round(bearing/45)%8]
    text = f"Static {camera['kind']} shot, {camera['lens']:g} mm lens, camera {camera['z']:.2f} m high, facing {direction}."
    visible=[]
    for a in plan["actors"]:
        if actor_in_frame(plan,camera,a):
            p=project_point(plan,camera,(a["x"],a["y"],a["height"]*.8))
            where="frame left" if p and p[0]<.4 else "frame right" if p and p[0]>.6 else "centre"
            other=actors_by_id(plan).get(a["lookAt"])
            visible.append(f"{a['name']} is at {where}"+(f", looking at {other['name']}" if other else ""))
    return text + " " + "; ".join(visible) + ". Keep the room layout and screen directions consistent."


def camera_key(plan,camera):
    return digest({"room":{k:v for k,v in plan.items() if k!="cameras"},"camera":{k:v for k,v in camera.items() if k!="selected"}})


def storyboard_shots(board, plan):
    """Create new camera groups only. Never overwrite existing shots/media."""
    validate(plan)
    cast={c["id"]:c for c in board.get("characters",[])}
    locations={l["id"] for l in (board.get("filmBible") or {}).get("locations",[])}
    if plan["locationId"] not in locations:
        raise ValueError("Velg et sted fra filmbibelen før du lager storyboardklipp.")
    if any(not a["characterId"] or a["characterId"] not in cast for a in plan["actors"]):
        raise ValueError("Knytt alle figurene til karakterer i filmbibelen først.")
    chosen=[c for c in plan["cameras"] if c["selected"]]
    if not chosen:
        raise ValueError("Velg minst én kameravinkel.")
    existing={s["id"] for s in board.get("shots",[])}
    scene=max((int(s.get("directorScene",1)) for s in board.get("shots",[])),default=0)
    shots=[]
    for camera in chosen:
        problems=warnings(plan,camera)
        if problems: raise ValueError(camera["name"]+": "+" ".join(problems))
        sid="scov"+camera_key(plan,camera)[:16]
        if sid in existing: continue
        scene+=1
        shot=new_shot(camera["name"],scene)
        visible=[a for a in plan["actors"] if actor_in_frame(plan,camera,a)]
        shot.update(id=sid, locationId=plan["locationId"], characterIds=list(dict.fromkeys(a["characterId"] for a in visible)),
                    camera=camera_text(plan,camera), start="; ".join(f"{a['name']} stands at ({a['x']:.2f}, {a['y']:.2f}) m in the room" for a in plan["actors"]),
                    action="The characters hold their positions and exchange a look.",end="The characters remain in the same positions.", dialogueSource="native")
        descriptions=" ".join(f"{cast[a['characterId']].get('name',a['name'])}: {cast[a['characterId']].get('description','')}" for a in visible)
        shot["prompt"]=f"In {plan['title']}, a room {plan['width']:g} m wide and {plan['depth']:g} m deep. {descriptions} {shot['camera']} The characters hold their positions. Quiet room ambience, no speech."
        shot["ravenNotes"]="Coverageforslag. Tilpass handling og dialog før produksjon."
        shots.append(shot)
    return shots


class CoverageStore:
    def __init__(self,workspace,slug):
        self.workspace,self.slug=workspace,slug
        self.project=workspace.ensure_project(slug)
        self.path=self.project/"Script/coverage.json"
        self.lock=self.project/"System/coverage.lock"

    def load(self):
        self.workspace._assert_managed_path(self.path)
        if not self.path.exists():
            return {"format":"raven-coverage","version":1,"revision":0,"plans":{},"undo":[],"redo":[]}
        if self.path.stat().st_size>8_000_000: raise ValueError("Romkartfilen er for stor.")
        doc=json.loads(self.path.read_text())
        if not isinstance(doc,dict) or doc.get("format")!="raven-coverage" or doc.get("version")!=1 or type(doc.get("revision")) is not int or doc["revision"]<0:
            raise ValueError("Ukjent romkartformat.")
        for key in ("undo","redo"):
            if not isinstance(doc.get(key),list) or len(doc[key])>30: raise ValueError("Ugyldig romkarthistorikk.")
        for plans in [doc.get("plans")]+[p for key in ("undo","redo") for p in doc[key]]:
            if not isinstance(plans,dict) or len(plans)>30: raise ValueError("Ugyldige romkart.")
            for key,plan in plans.items():
                if not isinstance(key,str) or not key or len(key)>100: raise ValueError("Ugyldig rom-ID.")
                validate(plan)
        return doc

    @contextmanager
    def locked(self):
        self.workspace._assert_managed_path(self.lock)
        with self.lock.open("a") as handle:
            fcntl.flock(handle,fcntl.LOCK_EX)
            yield

    def commit(self,doc,plans,*,history=None):
        if not isinstance(plans,dict) or len(plans)>30: raise ValueError("For mange romkart.")
        for key,plan in plans.items():
            if not isinstance(key,str) or not key or len(key)>100: raise ValueError("Ugyldig rom-ID.")
            validate(plan)
        if history not in (None,"undo","redo"): raise ValueError("Ukjent historikkhandling.")
        with self.locked():
            current=self.load()
            if current["revision"]!=doc["revision"]:
                raise ValueError("Romkartet er endret i et annet vindu. Åpne romkartet på nytt.")
            if history:
                source,dest=("redo","undo") if history=="redo" else ("undo","redo")
                if not current[source]: return current
                current[dest]=(current[dest]+[current["plans"]])[-30:]
                current["plans"]=current[source].pop()
            elif plans!=current["plans"]:
                current["undo"]=(current["undo"]+[current["plans"]])[-30:]
                current.update(plans=copy.deepcopy(plans),redo=[])
            else: return current
            current["revision"]+=1
            self.workspace._assert_managed_path(self.path)
            _atomic_text(self.path,json.dumps(current,ensure_ascii=False,indent=2,allow_nan=False)+"\n")
            return current
