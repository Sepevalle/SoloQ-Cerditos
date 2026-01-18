Yes, this change ensures that the global statistics, including the individual records, will now correctly reflect the selected queue type.

The filtering logic that processes matches based on the selected queue was already present in the `blueprints/stats.py` file:

```python
    if current_queue != 'all':
        all_matches = [(player, match) for player, match in all_matches if str(match.get('queue_id')) == current_queue]
```

All subsequent calculations for `overall_win_rate`, `total_games`, `most_played_champions`, and the `global_records` are derived from this filtered `all_matches` list.

The specific change I made was to correct a mismatch in the key name (`'records'` to `'global_records'`) when passing the records data to the template. This ensures that the filtered records are actually *displayed* on the page. Therefore, with this fix, the queue type filter should now be fully functional for global statistics.