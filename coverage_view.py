"""Real Raven painter/event surface for the room plan and camera preview."""
import math

import raven as r

from coverage import axis_basis, target, warnings
from coverage_render import COLORS


def transform(plan,width,height):
    scale=min(max(1,width-48)/plan["width"],max(1,height-48)/plan["depth"])
    return scale,(width-plan["width"]*scale)/2,(height-plan["depth"]*scale)/2


def to_pixel(plan,x,y,width,height):
    scale,ox,oy=transform(plan,width,height)
    return ox+x*scale,oy+(plan["depth"]-y)*scale


def from_pixel(plan,x,y,width,height):
    scale,ox,oy=transform(plan,width,height)
    return max(.15,min(plan["width"]-.15,(x-ox)/scale)),max(.15,min(plan["depth"]-.15,plan["depth"]-(y-oy)/scale))


def text(painter,x,y,value,size=12,color=(.85,.88,.87,1)):
    r.Rdraw_text(painter,r.R_INVALID_ID,size,x,y,str(value),*color)


def draw_plan(painter,width,height,plan,selected):
    r.Rdraw_rect(painter,0,0,width,height,10,.065,.09,.10,1)
    scale,ox,oy=transform(plan,width,height)
    rw,rh=plan["width"]*scale,plan["depth"]*scale
    r.Rdraw_rect(painter,ox,oy,rw,rh,0,.11,.16,.17,1)
    for i in range(math.ceil(plan["width"])+1):
        x=ox+i*scale
        r.Rdraw_line(painter,x,oy,x,oy+rh,1,.22,.29,.29,.6)
    for i in range(math.ceil(plan["depth"])+1):
        y=oy+i*scale
        r.Rdraw_line(painter,ox,y,ox+rw,y,1,.22,.29,.29,.6)
    r.Rdraw_rect_outline(painter,ox,oy,rw,rh,0,2,.55,.65,.64,1)
    text(painter,ox+rw/2-15,oy-24,"NORTH",10)
    text(painter,ox+8,oy+rh+8,f"{plan['width']:g} × {plan['depth']:g} m · grid 1 m",11)
    actors={a["id"]:a for a in plan["actors"]}
    if len(plan["axis"])==2:
        a,b=[actors[i] for i in plan["axis"]]
        try:
            along,_=axis_basis(plan)
            for i in range(-30,31):
                p=to_pixel(plan,a["x"]+along[0]*i*.3,a["y"]+along[1]*i*.3,width,height)
                q=to_pixel(plan,a["x"]+along[0]*(i*.3+.16),a["y"]+along[1]*(i*.3+.16),width,height)
                r.Rdraw_push_clip_rect(painter,ox,oy,rw,rh)
                r.Rdraw_line(painter,*p,*q,2,.92,.47,.45,1)
                r.Rdraw_pop_clip_rect(painter)
        except ValueError:pass
    for i,camera in enumerate(plan["cameras"]):
        x,y=to_pixel(plan,camera["x"],camera["y"],width,height)
        aim=target(plan,camera);tx,ty=to_pixel(plan,*aim[:2],width,height)
        distance=math.hypot(tx-x,ty-y) or 1
        dx,dy=(tx-x)/distance,(ty-y)/distance
        chosen=selected==("camera",camera["id"])
        color=(.94,.43,.39,1) if warnings(plan,camera) else (.94,.68,.46,1) if chosen else (.56,.68,.68,.8)
        r.Rdraw_line(painter,x,y,tx,ty,2 if chosen else 1,*color)
        r.Rdraw_triangle(painter,x+dx*11,y+dy*11,x-dx*7-dy*7,y-dy*7+dx*7,x-dx*7+dy*7,y-dy*7-dx*7,*color)
        text(painter,x+12,y-15,str(i+1),13,color)
        if chosen:
            sw=36 if plan["aspect"]>=1 else 36*plan["aspect"]
            angle=math.atan(sw/2/camera["lens"])
            for sign in (-1,1):
                vx=dx*math.cos(angle)-dy*math.sin(angle)*sign;vy=dy*math.cos(angle)+dx*math.sin(angle)*sign
                r.Rdraw_line(painter,x,y,x+vx*distance,y+vy*distance,1,*color)
            r.Rdraw_circle(painter,tx,ty,5,*color)
    for i,a in enumerate(plan["actors"]):
        x,y=to_pixel(plan,a["x"],a["y"],width,height)
        if selected==("actor",a["id"]):r.Rdraw_circle(painter,x,y,15,.95,.92,.81,1)
        r.Rdraw_circle(painter,x,y,11,*COLORS[i],1)
        other=actors.get(a["lookAt"])
        if other:
            dx,dy=other["x"]-a["x"],-(other["y"]-a["y"]);length=math.hypot(dx,dy) or 1
            r.Rdraw_line(painter,x,y,x+dx/length*23,y+dy/length*23,3,*COLORS[i],1)
        text(painter,x+15,y+5,a["name"][:24],12)


def draw_preview(painter,width,height,plan,camera,texture):
    r.Rdraw_rect(painter,0,0,width,height,8,.08,.10,.11,1)
    if not camera:
        text(painter,18,18,"Suggest cameras to preview framing.")
        return
    w=min(width,height*plan["aspect"]);h=w/plan["aspect"];ox=(width-w)/2;oy=(height-h)/2
    if texture != r.R_INVALID_ID:
        r.Rdraw_image(painter,texture,ox,oy,w,h,1,1,1,1)
    text(painter,12,10,"BLOCKING · IMAGE NOT GENERATED",10)
