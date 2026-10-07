# Dashboard design rules

How the dashboard behaves and looks. These rules are binding for any change to
the dashboard UI: a page that is added or redesigned follows all of them. The
Operations page and the Operations record settings follow them; the RAN page
follows them with the exception listed at the end. Older pages still use the
previous loader (`Loader.jsx`) and modals for some actions, and move to these
rules when they are redesigned.

The frontend's structure (routes, navigation, runtime configuration) is in
[contributing.md](contributing.md#dashboard-frontend). The building blocks named
below live in `dashboard/frontend/src/`.

## 1. Every change to the testbed is a piece

A button that changes the testbed starts a piece: a named, idempotent part of a
phase playbook, listed in `ansible/pieces.yml` and run by
`ansible/tools/kelt-piece` (see [contributing.md](contributing.md#pieces)). The
CLI runs the same pieces with `kelt run-piece`, so an action behaves the same
from both sides, and every run leaves a record.

Use `PieceButton` (`components/PieceButton.jsx`). It takes the title, the tier and
the confirmation text from the registry; the page follows the run.

Example: the RAN page's "Bring the RAN link up" fix starts the `ran_link` piece.

## 2. Three tiers by effect, three colours

| Tier | What it does | Colour |
|------|--------------|--------|
| READ | Reads, opens, copies; changes nothing | grey (slate) |
| CHANGE | Brings the testbed to the state it should already be in | indigo |
| DISRUPT | Interrupts traffic or removes something | amber |

A DISRUPT action is never grey. The tier of a piece is its `tier` in
`pieces.yml`.

`ConfirmAction` colours its button and its confirmation from the tier; a quiet
(secondary) button keeps a grey background but takes the tier's text colour.

Example: on the RAN page, "Open the gNB console" is READ, "Bring the RAN link
up" (the `ran_link` piece) is CHANGE, "Detach" is DISRUPT.

## 3. The confirmation opens inside the card

A CHANGE or DISRUPT action asks for confirmation in place: the card grows a panel
that says what the action runs, what it changes and how long it takes; a CHANGE
is then one click. A DISRUPT also says what stops, and its confirm button
repeats the effect. A piece that cuts devices off (`confirm_word` in
`pieces.yml`, e.g. Detach) also asks for that word to be typed before the
confirm button works. No modal for actions.

Use `ConfirmAction` (`components/Card.jsx`). The panel is a `t-acc` accordion
(`index.css`); its selectors are child combinators, so an accordion inside
another opens only itself.

## 4. A DISRUPT action has its own place

It sits at the foot of its card, under a divider, apart from the routine
controls. Its label names the effect ("Detach", not "Apply"). It is shown only
while it applies: nothing to detach, no Detach button.

Example: Detach at the foot of the RAN page's path card.

## 5. Operations: the header and the Operations page

The header's Operations button shows how many pieces are running, with the
loader while one is. Its panel lists at most three running operations and the
last result, and never scrolls; the rest is on the Operations page (Platform →
Operations), which lists the 50 newest runs from the dashboard and the CLI with
their steps and read-back; the Ansible output opens on request, or by itself
when the run failed. A button whose piece is running opens its
operation, or the Operations page when the run was started elsewhere.

Both are admin-only, like the operations API. The record's retention is in
Settings → Operations record.

## 6. One loader, only for work someone waits on

The loader is `SignalBars` (`components/SignalBars.jsx`): four bars, 16 px, in
the card header's slot (`Card`'s `busy`). It shows while someone waits for a
result: the first read of a card, a run they started. A page that polls shows no
loader for its polls and has no manual refresh button; it says how fresh its data
is with "updated N s ago" (`UpdatedAgo`).

## 7. Numbers resolve in place

A number that arrives or changes resolves digit by digit where it stands, with
no layout shift: `Num` (`components/Num.jsx`). With reduced motion the value is
shown directly.

## 8. Data comes from `useResource`

`data/useResource.js` reads one shared cache per key (`lib/resourceCache.js`):
what was read before is shown at once and refreshed in the background
(stale-while-revalidate). Polling pauses while the tab is hidden. A refresh never
rebuilds content that is already on screen. A failed refresh keeps the last data
and says it failed (`Card`'s `error` line).

## 9. Cards keep their own height

A grid of cards uses `items-start`, so opening a drawer or a confirmation in one
card does not stretch its neighbours.

## 10. House classes, no new UI dependencies

Section labels, cards and buttons use the existing classes and components:
`btn` and `inputCls` from `components/ui.jsx`, `Card`, the node and NF cards
(`NodeCard`, `NfCard`). A new UI library is not added for a page.

## 11. "Done" means the state was read back

A run that exits 0 is not yet done: the piece's `check` reads the state back,
and only then does the page say done. Until the read-back has run (it runs when
the page follows the run or the run is opened), the run reads "exited 0, not read
back yet". A failed read-back is a failure, shown as one. A run opened more than
10 minutes after it ended is marked "not read back" rather than judged by
today's state.

## 12. Values come from the backend

Addresses, ports, subnets and names shown on a page come from the backend, which
reads them from `ansible/group_vars/all.yml` or from the running pods. The UI
has no literals for them.

Example: the gNB settings on the RAN page (AMF address and NGAP port, RAN
gateway, user-plane route) are read from the backend; the NGAP port is read by
name from the running AMF pod.

## 13. One gate before the shell

The shell (sidebar, header, pages) opens only once the backend and Keycloak
have answered and there is a session. Every state before that is one frame,
`AuthGate` (`components/AuthGate.jsx`), chosen by `gateView` (`lib/authFlow.js`):
waiting for the services (each listed with its state), opening the sign-in
page, completing it, session ended, signed out, sign-in failed, HTTPS needed,
access session expired. The auth routes (`/auth/callback`, `/logged-out`) are
gate states, never pages inside the shell. Keycloak is probed only until the
shell opens.

A backend lost with the shell open does not unmount it: the same frame covers
the shell ("Backend not reachable"), with the service state and its last
messages and, for admins, a restart through the watchdog; it goes away by itself
when the backend answers. Meanwhile reads wait and are sent on recovery, so the
pages keep their data and their state; writes fail at once
(`lib/backendState.js`, `api.js`).
The gate is the only place with the spinner; inside the shell the loader is
`SignalBars` (6).

## 14. Toasts under the header

Short results any page raises (`useToast`, `context/ToastContext.jsx`) appear
at the top right, under the header, never over its controls. A state that holds
(an update available, an operation running) is in the header, where its click
leads to the place to act on it; it is not repeated as a toast.

## Where the dashboard does not follow these yet

- RAN page, the gNB console address: "Publish" and "Remove" call the API
  directly; "Publish" has no confirmation panel.
- Pages not yet redesigned: the previous loader (`Loader.jsx`), modals for some
  actions, manual refresh buttons.
