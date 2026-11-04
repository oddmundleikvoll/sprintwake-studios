#!/usr/bin/env python3
"""Small PyAV-backed ffprobe-compatible subset used by Raven."""
import argparse, json, sys
import av

p = argparse.ArgumentParser()
p.add_argument("-v", default="error"); p.add_argument("-select_streams", default="")
p.add_argument("-show_entries", default=""); p.add_argument("-print_format", default="")
p.add_argument("-show_format", action="store_true"); p.add_argument("-show_streams", action="store_true")
p.add_argument("-of", default="default=nw=1:nk=1"); p.add_argument("path")
a = p.parse_args()
try:
    with av.open(a.path) as source:
        if a.print_format == "json" and (a.show_format or a.show_streams):
            streams = []
            for stream in source.streams:
                item = {"index": stream.index, "codec_type": stream.type}
                if stream.type == "video":
                    rate = stream.average_rate or stream.base_rate
                    item.update(width=stream.width, height=stream.height, nb_frames=str(stream.frames),
                                r_frame_rate=str(rate) if "/" in str(rate) else str(rate) + "/1")
                streams.append(item)
            print(json.dumps({"streams": streams, "format": {"duration": str((source.duration or 0) / av.time_base)}}))
        else:
            group, _, fields = a.show_entries.partition("=")
            if group == "format" and fields == "duration": print((source.duration or 0) / av.time_base)
            elif group == "stream":
                streams = list(source.streams)
                if a.select_streams:
                    kind, _, index = a.select_streams.partition(":")
                    streams = [s for s in streams if s.type == {"v": "video", "a": "audio"}.get(kind, kind)]
                    if index: streams = streams[int(index):int(index)+1]
                for stream in streams:
                    values = [str(getattr(stream, field) if field != "duration" else float(stream.duration * stream.time_base) if stream.duration is not None else 0) for field in fields.split(",")]
                    print(",".join(values) if a.of.startswith("csv") else "\n".join(values))
            else: raise ValueError("Unsupported probe entries: " + a.show_entries)
except Exception as error:
    print(error, file=sys.stderr); raise SystemExit(1)
