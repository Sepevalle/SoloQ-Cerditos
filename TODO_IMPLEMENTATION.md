# TODO: Fix Match History GitHub Size Limits

## Implementation Steps

### Phase 1: Update services/github_service.py ✅ COMPLETED
- [x] Add utility functions for size calculation (estimate_payload_size)
- [x] Define MAX_B64_BYTES threshold
- [x] Implement v3 chunking in save_player_match_history()
- [x] Update read_player_match_history() to support v2/v3 format
- [x] Add defensive logging in write_file_to_github()
- [x] Add retry logic with backoff for SHA conflicts and rate limits

### Phase 2: Update validate_lp_assignments.py ✅ COMPLETED
- [x] Update validate_match_lp_assignments() to support folder-based format
- [x] Support both legacy .json files and v2/v3 folder structure


### Phase 3: Testing ⏳ PENDING
- [ ] Test with small player (legacy format)
- [ ] Test with medium player (v2 weekly)
- [ ] Test with large player (v3 chunked)
