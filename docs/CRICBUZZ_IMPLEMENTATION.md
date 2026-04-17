# Cricbuzz Scraper Implementation

## Background
ESPN Cricinfo blocks requests from datacenter IPs (like Render), returning 403 Forbidden errors. Cricbuzz does not have this restriction and works from Render.

## Solution
Added Cricbuzz as an alternative cricket data source, configurable per-event in the admin panel, while preserving the existing ESPN Cricinfo scraper.

## Configuration
Each event has a `score_source` field that can be set to:
- `cricinfo` (default) - Uses ESPN Cricinfo
- `cricbuzz` - Uses Cricbuzz

Set this in the admin panel when creating/editing an event. Changes take effect on the next scrape cycle without requiring a server restart.

## Implementation Status

### Step 1: Analyze Cricbuzz Page Structure - COMPLETE
- [x] Fetched live match page from Cricbuzz
- [x] Identified data in HTML (no `__NEXT_DATA__`, uses div classes)
- [x] Mapped data structure to existing format

### Step 2: Create `scrape_cricbuzz()` Function - COMPLETE
- [x] Added `scrape_cricbuzz()` in `scraper.py`
- [x] Parses score header div with pattern like "AUS 371 & 349 ENG 286 & 207 / 6 (63)"
- [x] Extracts match status (Day X: Stumps/Live/etc.)
- [x] Returns data in same format as `scrape_cricket()`

### Step 3: Add Source Selection Logic - COMPLETE
- [x] Added `CRICKET_SOURCE` env variable in `scraper.py`
- [x] Updated `scrape_event()` to check env var and call appropriate scraper
- [x] Falls back to cricinfo URL if cricbuzz_url not set

### Step 4: Update Event Configuration - COMPLETE
- [x] Added `cricbuzz_url` field to events table
- [x] Updated admin panel with Cricbuzz URL input field
- [x] Updated app.py and app_local.py API endpoints

### Step 5: Testing - IN PROGRESS
- [x] Test locally with `CRICKET_SOURCE=cricbuzz`
- [ ] Deploy to Render with `CRICKET_SOURCE=cricbuzz`
- [ ] Verify data is being captured correctly

### Step 6: Cleanup - PENDING
- [ ] Remove `/test-cricbuzz` endpoint
- [ ] Update CLAUDE.md if needed

## Data Format (must match existing)
```python
{
    'status': 'Live',           # Match status text
    'innings': [
        {
            'team': 'Australia',
            'inning_number': 1,
            'runs': 245,
            'wickets': 6,
            'overs': 78.3
        },
        ...
    ]
}
```

## Cricbuzz URL Format
```
https://www.cricbuzz.com/live-cricket-scores/{match_id}/{slug}
```

Example:
```
https://www.cricbuzz.com/live-cricket-scores/108801/aus-vs-eng-3rd-test-the-ashes-2025-26
```

## Rollback Plan
If Cricbuzz implementation fails:
1. Set `CRICKET_SOURCE=cricinfo` (or remove the variable)
2. Original ESPN scraper will be used
3. Accept that cricket scores won't work from Render (odds still work)
