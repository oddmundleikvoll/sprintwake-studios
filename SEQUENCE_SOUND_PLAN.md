# Narrative structure and independent sound tracks

> Historical implementation plan, delivered in 0.6.0. Native narrative structure and independent sound tracks are implemented.
> Current goal, status and remaining work: [PROJECT.md](PROJECT.md), [STATUS.md](STATUS.md).

User-authorized scope: native sequence/scene management with runtime targets,
plus editable dialogue, music, ambience and effects crossing picture cuts.

1. Keep canonical shots, production camera groups and receipts unchanged. Store
   narrative assignments/goals in Script/structure.json and audio edits in
   Script/sound.json. Both use locked CAS, atomic writes and persistent undo.
2. Derive actual runtimes from the saved cut, including trim/exclusion. Sequence
   order affects picture only through an explicit apply-to-cut action. Targets
   guide editing and never stretch media or launch generation.
3. Provide native structure cards and a dedicated multi-lane sound Timeline.
   Sound cues have absolute film times, independent source trims, fades and dB
   gain; mute/solo operate per bus. Original take audio is an explicitly labelled
   combined source, never presented as automatically separated dialogue/stems.
4. Preserve imported audio as verified Input copies. Stream FFmpeg mixing into
   48 kHz stereo PCM with exact 24 fps sample offsets. Preview and rough-cut export
   consume the same saved mix; missing media fails visibly. Model inference is
   outside this editing flow.
5. Verify persistence/CAS, hierarchy/reassignment, actual runtime and source
   preservation, synthetic tone timing/overlap/fades/mute/solo, actual exported
   sound and native mouse editing/reopen. Run documented checks, independent
   review, inspect real-window captures, update docs and package the same app.

Ownership: sequence.py and its tests; sound.py/sound_media.py and their tests;
primary owns native workspace, cut export integration, smoke, docs and packaging.
Baseline copy: /var/folders/qy/t49k9zg924s3r6d23dtjnkxr0000gn/T/raven-sequence-sound-baseline-9nbl5j79
