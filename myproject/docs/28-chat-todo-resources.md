# 28 — Chat, Todo & Resources

Three small internal-tools apps.

---

# Chat — internal staff messaging

Mounted at `/chat/`. All views `@login_required`.

## Pages

| Page | URL | View | Template |
|---|---|---|---|
| Inbox | `/chat/` | `inbox` | `chat/inbox.html` |
| Thread | `/chat/thread/<id>/` | `thread` | `chat/thread.html` |
| Start a chat with someone | `/chat/start/<user_id>/` | | redirects into a thread |

## API endpoints

| URL | Purpose |
|---|---|
| `/chat/api/create-group/` | Create a group thread |
| `/chat/api/send/` | Send a message |
| `/chat/api/messages/<thread_id>/` | Poll for messages |
| `/chat/api/unread-count/` | Navbar badge |
| `/chat/api/mark-read/<thread_id>/` | |
| `/chat/api/message/<id>/delete\|edit/` | |
| `/chat/api/thread/<id>/remove-member\|add-members\|leave\|delete-group\|delete\|rename/` | Group management |

## Models

| Model | Line | Notes |
|---|---|---|
| `ChatThread` | `chat/models.py:6` | 1-on-1 or group. Properties `other_participant`, `display_name` |
| `ChatMessage` | `chat/models.py:55` | |

## Live updates

`chat.context_processors.unread_message_count` supplies `unread_chat_count` to **every**
template render. `templates/base.html:2704` polls `pollUnreadCount` every **3 seconds** —
the most frequent poll in the project.

## Gotcha

`api_unread_count` (`chat/views.py:273`) has `@require_http_methods` but **no
`@login_required`**. It guards internally by checking `request.user.is_authenticated`, so
it is not an access hole — but it is inconsistent with the rest of the app.

---

# Todo — internal task board

Mounted at `/todo/`.

## Access rule

`@login_required` + `@todo_access_required` (`todo/views.py:17`), which allows:

- admins, **or**
- users with `can_access_todo`, **or**
- **any user who has a task assigned to or created by them**

That third clause means a user with no Todo permission can still reach the board once
someone assigns them a task.

`can_manage_tasks(user)` is **admin-only** and controls creating and assigning.

## Pages

| Page | URL | View | Template |
|---|---|---|---|
| Kanban board | `/todo/` | `board` | `todo/board.html` |
| Create task | `/todo/task/create/` | | `todo/task_form.html` |
| Edit task | `/todo/task/<id>/edit/` | | `todo/task_form.html` |
| Task detail | `/todo/task/<id>/` | | `todo/task_detail.html` |
| Delete | `/todo/task/<id>/delete/` | | POST |
| Change status (drag-drop) | `/todo/api/task/<id>/status/` | | POST JSON |

## Models

| Model | Line |
|---|---|
| `Task` | `todo/models.py:6` |
| `TaskComment` | `todo/models.py:68` |

## Cross-app behaviour

`_notify_assignee()` sends an **internal chat message** when a task is assigned — so Todo
depends on the Chat app.

---

# Resources — internal knowledge base

Mounted at `/resources/`.

## Access rule

`@login_required` + `@resources_access_required` (`resources/views.py:16`) — admin,
`can_view_resources`, or `can_create_resources`. Write operations additionally check
`can_manage_resources()`.

## Pages

| Page | URL | Template |
|---|---|---|
| Resource list | `/resources/` | `resources/list.html` |
| Resource detail | `/resources/<pk>/` | `resources/detail.html` |
| Create / edit | `/resources/create/`, `/resources/<pk>/edit/` | `resources/form.html` |

Actions (POST JSON): `<pk>/delete/`, `<pk>/mark-read/`,
`<pk>/file/<file_id>/delete/`, and `topics/create|<pk>/edit|<pk>/delete/`.

## Models

| Model | Line | Purpose |
|---|---|---|
| `ResourceTopic` | `resources/models.py:11` | Categories |
| `Resource` | `:63` | An article |
| `ResourceFile` | `:110` | Attachments |
| `ResourceRead` | `:180` | **Read receipts** — who has acknowledged which resource |

`ResourceRead` is what makes "mark as read" meaningful: you can see who has actually
consumed a policy document.

---

## Gotchas

- **Chat polls every 3 seconds.** It is the heaviest background poll in the project;
  consider it when diagnosing load.
- **Todo access leaks through assignment.** Assigning a task grants board access.
- **Task creation is admin-only**, which surprises staff who can see the board.
- Resources permission is checked by a **local** decorator, not the shared one.
- `resources` and `sentinel` are **missing from `CLAUDE.md`'s app map** — see
  [A4](./A4-appendix-known-quirks.md).

---

## Files that own this

- `chat/models.py`, `views.py`, `urls.py`, `context_processors.py`
- `todo/models.py`, `views.py`, `urls.py`
- `resources/models.py`, `views.py`, `urls.py`
- `templates/base.html:2704` — the chat poll
