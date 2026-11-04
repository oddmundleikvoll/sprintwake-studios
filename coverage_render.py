"""Small deterministic layout renderer. Proxies are geometry, not AI images."""
from __future__ import annotations

import math
from pathlib import Path
import struct
import zlib

from coverage import camera_basis

COLORS = [(0.88,.57,.32),(.30,.65,.86),(.62,.78,.40),(.78,.48,.72),(.84,.76,.37),(.38,.75,.68),(.65,.60,.86),(.84,.48,.46)]


def triangles(plan, *, empty=False):
    result=[]
    def quad(a,b,c,d,color):
        result.extend([((a,b,c),color),((a,c,d),color)])
    w,d,h=plan["width"],plan["depth"],plan["height"]
    quad((0,0,0),(w,0,0),(w,d,0),(0,d,0),(.44,.48,.48))
    quad((0,d,0),(w,d,0),(w,d,h),(0,d,h),(.69,.73,.72))
    quad((0,0,0),(0,d,0),(0,d,h),(0,0,h),(.58,.64,.64))
    quad((w,d,0),(w,0,0),(w,0,h),(w,d,h),(.64,.68,.67))
    quad((w,0,0),(0,0,0),(0,0,h),(w,0,h),(.62,.68,.68))
    if empty: return result
    by_id={a["id"]:a for a in plan["actors"]}
    for index,a in enumerate(plan["actors"]):
        color=COLORS[index%len(COLORS)]
        look=by_id.get(a["lookAt"])
        dx,dy=(look["x"]-a["x"],look["y"]-a["y"]) if look else (0,1)
        length=math.hypot(dx,dy) or 1
        front=(dx/length,dy/length);right=(front[1],-front[0]);size=a["height"]/1.7
        def point(x,y,z):
            return (a["x"]+(right[0]*x+front[0]*y)*size,a["y"]+(right[1]*x+front[1]*y)*size,z*size)
        def box(x,y,z,sx,sy,sz):
            p=[point(x+i*sx/2,y+j*sy/2,z+k*sz/2) for i,j,k in [(-1,-1,-1),(1,-1,-1),(1,1,-1),(-1,1,-1),(-1,-1,1),(1,-1,1),(1,1,1),(-1,1,1)]]
            for face,shade in [((0,1,2,3),.65),((4,7,6,5),1),((0,4,5,1),.8),((1,5,6,2),.9),((2,6,7,3),1),((3,7,4,0),.75)]:
                quad(*(p[i] for i in face),tuple(c*shade for c in color))
        box(0,0,1.12,.42,.26,.48)
        box(0,0,.82,.34,.24,.18)
        box(-.115,0,.37,.15,.18,.74);box(.115,0,.37,.15,.18,.74)
        box(-.30,0,1.03,.12,.15,.57);box(.30,0,1.03,.12,.15,.57)
        box(-.115,.08,.045,.17,.32,.09);box(.115,.08,.045,.17,.32,.09)
        top=point(0,0,1.7);bottom=point(0,0,1.40)
        ring=[point(.14,0,1.55),point(0,.13,1.55),point(-.14,0,1.55),point(0,-.13,1.55)]
        for i in range(4):
            result.extend([((top,ring[i],ring[(i+1)%4]),color),((bottom,ring[(i+1)%4],ring[i]),tuple(c*.8 for c in color))])
        result.append(((point(-.045,.10,1.56),point(.045,.10,1.56),point(0,.23,1.53)),(.96,.91,.78)))
    return result


def projected_triangles(plan,camera,*,empty=False):
    forward,right,up=camera_basis(plan,camera)
    sw=36 if plan["aspect"]>=1 else 36*plan["aspect"]
    sh=sw/plan["aspect"]
    def local(p):
        v=[p[i]-camera[k] for i,k in enumerate(("x","y","z"))]
        return [sum(a*b for a,b in zip(v,axis)) for axis in (right,up,forward)]
    result=[]
    for triangle,color in triangles(plan,empty=empty):
        polygon=[local(p) for p in triangle]
        clipped=[]
        for i,p in enumerate(polygon):
            q=polygon[(i+1)%len(polygon)]
            if p[2]>=.05:clipped.append(p)
            if (p[2]>=.05)!=(q[2]>=.05):
                t=(.05-p[2])/(q[2]-p[2]);clipped.append([p[k]+t*(q[k]-p[k]) for k in range(3)])
        for i in range(1,len(clipped)-1):
            points=[clipped[j] for j in (0,i,i+1)]
            result.append(([(.5+p[0]*camera["lens"]/sw/p[2],.5-p[1]*camera["lens"]/sh/p[2],p[2]) for p in points],color))
    return result


def render_pixels(plan,camera,width=480,*,empty=False):
    height=round(width/plan["aspect"])
    pixels=bytearray(bytes((25,32,35,255))*width*height)
    depths=[float("inf")]*(width*height)
    for points,color in projected_triangles(plan,camera,empty=empty):
        (ax,ay,az),(bx,by,bz),(cx,cy,cz)=[(p[0]*width,p[1]*height,p[2]) for p in points]
        area=(by-cy)*(ax-cx)+(cx-bx)*(ay-cy)
        if abs(area)<1e-8:continue
        xmin=max(0,math.floor(min(ax,bx,cx)));xmax=min(width-1,math.ceil(max(ax,bx,cx)))
        ymin=max(0,math.floor(min(ay,by,cy)));ymax=min(height-1,math.ceil(max(ay,by,cy)))
        rgba=bytes([max(0,min(255,round(c*255))) for c in color]+[255])
        for y in range(ymin,ymax+1):
            for x in range(xmin,xmax+1):
                u=((by-cy)*(x+.5-cx)+(cx-bx)*(y+.5-cy))/area
                v=((cy-ay)*(x+.5-cx)+(ax-cx)*(y+.5-cy))/area
                t=1-u-v
                if min(u,v,t)<-1e-8:continue
                inv=u/az+v/bz+t/cz
                if inv<=0:continue
                z=1/inv;index=y*width+x
                if z<depths[index]:
                    depths[index]=z;pixels[index*4:index*4+4]=rgba
    return bytes(pixels),width,height


def write_layout(path,plan,camera,*,width=480):
    pixels,width,height=render_pixels(plan,camera,width)
    def chunk(name,data):
        return struct.pack(">I",len(data))+name+data+struct.pack(">I",zlib.crc32(name+data))
    rows=b"".join(b"\0"+pixels[y*width*4:(y+1)*width*4] for y in range(height))
    Path(path).write_bytes(b"\x89PNG\r\n\x1a\n"+chunk(b"IHDR",struct.pack(">IIBBBBB",width,height,8,6,0,0,0))+chunk(b"IDAT",zlib.compress(rows,6))+chunk(b"IEND",b""))
