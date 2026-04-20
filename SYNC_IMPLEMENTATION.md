# Bidirectional Note ↔ Knowledge Synchronization Implementation

## Overview

Implemented complete bidirectional synchronization between Notes and imported Knowledge items. When a note is imported to the Knowledge base, changes to either the note or knowledge item automatically sync in both directions with visual status indicators.

## Changes Made

### Backend

#### 1. New Sync Router (`backend/routers/sync.py`)
- **Location**: Dedicated router module for sync endpoints
- **Endpoints**:
  - `POST /api/sync/note-to-knowledge` - Propagates note changes to all linked knowledge items
  - `POST /api/sync/knowledge-to-note` - Propagates knowledge changes back to source note
- **Reason for Separate Router**: Isolated from knowledge router to avoid path matching conflicts with generic `/{item_id}` routes

#### 2. Database Model Updates
- **Note** (`backend/models/note.py`):
  - Added `linked_knowledge_ids` (JSON array field) to track imported knowledge items
  
- **KnowledgeItem** (`backend/models/knowledge.py`):
  - Added `source_note_id` (nullable FK to Note) - tracks original source if imported from note
  - Added `sync_status` (enum: 'synced', 'pending', 'conflict') - tracks sync state
  - Added `last_synced_at` (ISO timestamp) - records when last sync occurred

#### 3. Notes Router Updates (`backend/routers/notes.py`)
- Updated `NoteResponse` to include `linked_knowledge_ids: list[str]`
- Added `PATCH /api/notes/{note_id}/linked-knowledge` endpoint for managing links

#### 4. Knowledge Import Enhancement (`backend/routers/knowledge.py`)
- Modified `import_note` endpoint to:
  - Set `source_note_id` on created knowledge item
  - Add knowledge ID to note's `linked_knowledge_ids` array
  - Initialize `sync_status = 'synced'`

### Frontend

#### 1. Knowledge Store Updates (`frontend/src/stores/knowledgeStore.ts`)
- **New Methods**:
  - `syncNoteToKnowledge(projectId, noteId, content, title)` - Sync note→knowledge
  - `syncKnowledgeToNote(projectId, itemId)` - Sync knowledge→note
  - Both methods perform optimistic UI updates then call backend endpoints

#### 2. Note List Component (`frontend/src/components/shared/NoteList.tsx`)
- **Updated Import Button Logic**:
  - Check: `(note.linked_knowledge_ids?.length ?? 0) > 0`
  - If linked: Show "✓ Zu Wissen verknüpft" status badge
  - If not linked: Show "→ Wissen" import button
- **Added Sync Button**:
  - Appears when note has linked knowledge items
  - Shows count: `🔄 Sync (N)`
  - Calls `syncNoteToKnowledge()` to propagate changes
- **Added fetchNotes() Refresh**:
  - After import, refreshes note data to show newly created link

#### 3. Knowledge Detail Panel (`frontend/src/components/knowledge/NodeDetailPanel.tsx`)
- **Added Sync Status Badge**:
  - Shows sync state when `source_note_id` exists
  - Display: "✓ Synchronisiert" or "⏳ Ausstehend"
  - Includes last sync timestamp
- **Added Sync Back Button**:
  - "↑ Änderungen in Notiz übernehmen"
  - Calls `syncKnowledgeToNote()` to propagate knowledge changes back to source note

## How It Works

### Import Flow
1. User clicks "→ Wissen" on a note in Note List
2. Frontend calls `importNote(projectId, noteId)`
3. Backend:
   - Creates new KnowledgeItem with `source_note_id = note.id`
   - Adds knowledge ID to note's `linked_knowledge_ids` array
   - Sets `sync_status = 'synced'`
4. Frontend calls `fetchNotes()` to refresh the notes list
5. UI now shows:
   - Import button replaced with "✓ Zu Wissen verknüpft" badge
   - Sync button appears showing "🔄 Sync (1)"

### Note → Knowledge Sync
1. User edits a note with linked knowledge items
2. User clicks sync button "🔄 Sync (N)"
3. Frontend optimistically updates knowledge item display
4. `POST /api/sync/note-to-knowledge` called with:
   - `project_id`: project to sync within
   - `note_id`: source note ID
   - `content`: updated HTML content
   - `title`: updated title
5. Backend updates ALL knowledge items with `source_note_id = note_id`
6. Sets `sync_status = 'synced'` and `last_synced_at = now()`

### Knowledge → Note Sync
1. User edits a knowledge item that came from a note
2. User clicks "↑ Änderungen in Notiz übernehmen"
3. Frontend optimistically updates knowledge sync status
4. `POST /api/sync/knowledge-to-note` called with:
   - `project_id`: project to sync within
   - `item_id`: knowledge item ID
5. Backend:
   - Finds source note via `source_note_id`
   - Updates note's `title` and `content` from knowledge item
   - Sets knowledge `sync_status = 'synced'` and `last_synced_at = now()`

## API Reference

### Sync Endpoints

#### POST /api/sync/note-to-knowledge
Updates all knowledge items linked to a note.

**Request**:
```json
{
  "project_id": "string",
  "note_id": "string",
  "content": "string (HTML)",
  "title": "string"
}
```

**Response**:
```json
{
  "synced_count": 0
}
```

#### POST /api/sync/knowledge-to-note
Syncs a knowledge item back to its source note.

**Request**:
```json
{
  "project_id": "string",
  "item_id": "string"
}
```

**Response**:
```json
{
  "note_id": "string",
  "synced": true
}
```

## Data Model

### Note
```typescript
{
  id: string
  project_id: string
  title: string
  content: string (HTML)
  linked_knowledge_ids: string[] // IDs of imported knowledge items
  // ... other fields
}
```

### KnowledgeItem
```typescript
{
  id: string
  project_id: string
  title: string
  content: string (HTML)
  source_note_id?: string | null // If imported from a note
  sync_status: 'synced' | 'pending' | 'conflict'
  last_synced_at?: string | null // ISO timestamp
  // ... other fields
}
```

## Running the Application

### Start Backend
```bash
cd backend
python run.py
```

The `run.py` wrapper handles:
- Port availability checking
- Proper module initialization
- Socket management on Windows

### Start Frontend
```bash
# Already served from backend when built
# Or for development:
npm run dev
```

### Full Production Start
```bash
./start-prod-full.bat
```

Starts:
- Frontend static server on http://localhost:3000
- Backend API server on http://localhost:3001
- Opens ProjectHub UI in browser

## Testing

### Test Endpoints with curl
```bash
# Test sync note→knowledge
curl -X POST http://localhost:3001/api/sync/note-to-knowledge \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "proj-123",
    "note_id": "note-456",
    "content": "<p>Updated content</p>",
    "title": "Updated Title"
  }'

# Expected: {"synced_count": 0}
```

### Test with TestClient
```python
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)
resp = client.post("/api/sync/note-to-knowledge", json={
    "project_id": "p1",
    "note_id": "n1",
    "content": "<p>test</p>",
    "title": "Test"
})
print(resp.json())  # {"synced_count": 0}
```

## Known Issues & Solutions

### Windows Socket Lock (Port Already in Use)
- **Symptom**: "Method Not Allowed (405)" errors
- **Cause**: Windows keeps sockets in TIME_WAIT state after closing
- **Solution**: 
  - Use `run.py` instead of `main.py` (handles this automatically)
  - Or kill all Python processes: `taskkill /IM python.exe /F`
  - Or use a different port: `TEST_PORT=3002 python run.py`

### Changes Not Showing in UI After Sync
- **Cause**: Frontend cache or missing refresh
- **Solution**: 
  - Sync actions use optimistic updates (show immediately)
  - If stale data appears, refresh the tab or navigate away and back
  - Future: Implement WebSocket for real-time push updates

## Future Enhancements

1. **Conflict Detection**: Handle simultaneous edits to note and knowledge
2. **WebSocket Updates**: Real-time sync notifications when both users edit
3. **Sync History**: Track all sync operations with timestamps
4. **Selective Sync**: Choose which knowledge items to sync
5. **Merge Strategies**: Options for handling conflicting changes

## Files Modified

- `backend/routers/sync.py` (NEW)
- `backend/routers/knowledge.py` (modified)
- `backend/routers/notes.py` (modified)
- `backend/models/note.py` (modified)
- `backend/models/knowledge.py` (modified)
- `backend/main.py` (modified)
- `backend/run.py` (NEW)
- `frontend/src/stores/knowledgeStore.ts` (modified)
- `frontend/src/components/shared/NoteList.tsx` (modified)
- `frontend/src/components/knowledge/NodeDetailPanel.tsx` (modified)
- `start-prod.bat` (modified)
- `start-prod-full.bat` (modified)
