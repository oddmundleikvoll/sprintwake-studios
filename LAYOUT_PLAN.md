# Shared native editing layout — 2026-11-04

> Historical implementation plan, delivered in 0.7.0. The Current paragraph below describes the starting point, not the installed layout.
> Current goal, status and remaining work: [PROJECT.md](PROJECT.md), [STATUS.md](STATUS.md).

Requested: compact scene list on the left, persistent central player, contextual
inspector on the right, timeline below; retain Copper colors and New York/SFNS.

Current: film/cut share a partial shell, while scenes, structure and sound replace
it with full pages. Sidebar is a tall list of thumbnails; structure stacks large
cards. The same native AVPlayer is shared by shot playback and modal previews.

Implementation order:
1. Shared XML shell for film/scenes/cut/structure/sound, timeline across its bottom,
   compact mode navigation and responsive side widths. No native renderer changes.
2. Pure scene/sequence row model plus native sidebar/inspector routing. Preserve
   shot IDs across canonical/editorial order and nested scene selection. Structure
   actions remain backed by SequenceStore; sound uses its existing journal/widget.
3. Reuse saved mix preparation and the single player for inline editorial preview;
   preserve actual production review in its existing modal, with no inferred approvals.
4. Focused model/selection/layout tests, actual-window normal/compact geometry,
   scroll/selection/trim/undo/take/sound checks; existing full checks, independent
   review and installed-bundle update.

Acceptance: scene/shot selection remains stable across editing modes; selected
scene/group details appear on the right; main viewer and correct timeline stay
visible without overlap at 1440x920 and 1180x760; sound has all four lanes and
cross-cut editing; persisted takes/trims/history and canonical production stay
intact. No inference or mutation of user productions for implementation QA.
