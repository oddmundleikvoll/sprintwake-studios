"""Timecode and source-preserving audio gestures, without native dependencies."""
import copy
import math


def time_text(frames):
    minutes, seconds = divmod(frames / 24,60)
    return f"{int(minutes):02}:{seconds:05.2f}"


def time_frames(text):
    parts=text.strip().replace(",", ".").split(":")
    if not 1<=len(parts)<=3: raise ValueError("Enter seconds, mm:ss or hh:mm:ss.")
    values=[float(p) for p in parts]
    if any(not math.isfinite(n) or n<0 for n in values) or any(n>=60 for n in values[1:]):
        raise ValueError("Time must be positive, with minutes/seconds below 60 after the colon.")
    total=sum(n*60**i for i,n in enumerate(reversed(values)))
    if total>86400: raise ValueError("Time cannot exceed 24 hours.")
    return round(total*24)


def sound_gesture(state, changes, removed=()):
    result=copy.deepcopy(state)
    result["clips"]=[c for c in result["clips"] if c["id"] not in removed]
    for clip in result["clips"]:
        if clip["id"] not in changes: continue
        start,length,track=changes[clip["id"]]
        old_start,old_length=clip["atFrame"],clip["outFrame"]-clip["inFrame"]
        start=max(0,start);length=max(1,length)
        if length!=old_length:
            if start!=old_start and abs(start+length-old_start-old_length)<=1:
                new_in=max(0,min(clip["outFrame"]-1,clip["inFrame"]+start-old_start))
                clip["atFrame"]=max(0,old_start+new_in-clip["inFrame"])
                clip["inFrame"]=new_in
            else:
                clip["outFrame"]=max(clip["inFrame"]+1,min(clip["sourceFrames"],clip["inFrame"]+length))
        else: clip["atFrame"]=start
        clip["track"]=track
        new_length=clip["outFrame"]-clip["inFrame"]
        clip["fadeIn"]=min(clip["fadeIn"],new_length)
        clip["fadeOut"]=min(clip["fadeOut"],new_length)
    return result
