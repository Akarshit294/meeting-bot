"""Optional in-call features that must never touch the call: each person's camera picture (task 11) and
the speaker timeline (task 12). Each is an object with tick() and stop(), or None."""
from __future__ import annotations


def start(call):
    pictures = speakers = None
    rep, args, cfg = call.rep, call.args, call.cfg
    try:
        endpoint = cfg.endpoint_for("video_separate_png.data", "websocket")
        if args.no_camera_pictures:
            rep.log("pictures: off (--no-camera-pictures)")
        elif endpoint is None:
            rep.log("pictures: no video_separate_png.data endpoint, so no camera pictures are sent")
        else:
            import pictures as pictures_module

            pictures = pictures_module.CameraPictures(call.page, call.people, cfg.bot_id, endpoint.url, rep)
    except Exception as exc:  # noqa: BLE001 - never let this touch the call
        rep.log(f"pictures: not started ({type(exc).__name__}: {exc})")
    try:
        import speakers as speakers_module

        speakers = speakers_module.SpeakerTimeline(call.page, call.people, rep)
    except Exception as exc:  # noqa: BLE001
        rep.log(f"speakers: not started ({type(exc).__name__}: {exc})")
    return pictures, speakers
