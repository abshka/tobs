# Fix: Takeout Approval Waiting Logic

## Problem
Previously, when Takeout required user confirmation:
1. System would request Takeout
2. Immediately fail with error if not already approved
3. User had no time to go to Telegram and approve

**Result**: User couldn't approve in time, export failed immediately.

## Solution
Added automatic retry logic with proper waiting:

### Changes Made

#### 1. `src/export/exporter.py` - TakeoutSessionWrapper.__aenter__()
- **Added**: Retry loop (60 attempts × 5 seconds = 5 minutes total)
- **Added**: Progress logging every 30 seconds
- **Behavior**: 
  - First attempt sends Takeout request
  - If TakeoutInitDelayError: wait 5s and retry
  - Shows progress: "⏳ Still waiting... (30s elapsed, 270s remaining)"
  - After 5 minutes: timeout error

#### 2. `main.py` - precheck_takeout()
- **Updated**: User messaging to indicate automatic retry
- **Changed**: "Waiting for Takeout session initialization..." 
  → "System will automatically check for confirmation every 5 seconds..."
- **Updated**: Error message to reflect timeout (not immediate failure)

## How It Works Now

```
User runs export
  ↓
System: "⚠️ Check Telegram for Takeout request"
System: "⏳ Will check every 5 seconds for up to 5 minutes..."
  ↓
[Attempt 1] → TakeoutInitDelayError → wait 5s
[Attempt 2] → TakeoutInitDelayError → wait 5s
...
[User approves in Telegram at ~30s]
...
[Attempt 7] → Success! ✅
  ↓
Export starts
```

## User Experience

**Before**:
```
⚠️ Check Telegram for Takeout request
⏳ Waiting...
❌ Takeout permission required!
   (fails immediately, no actual waiting)
```

**After**:
```
⚠️ Check Telegram for Takeout request
⏳ System will automatically check every 5 seconds...
   [User has time to open Telegram]
   [User approves request]
⏳ Still waiting for Takeout approval... (30s elapsed, 270s remaining)
✅ Manual Takeout Init Successful. ID: 12345
```

## Testing Recommendations

1. **Test timeout behavior**:
   - Run export but DON'T approve
   - Should timeout after 5 minutes with clear message

2. **Test successful approval**:
   - Run export
   - Approve within 1-2 minutes
   - Should proceed to export

3. **Test immediate approval**:
   - Run export
   - Approve within first 10 seconds
   - Should succeed on early attempt

## Configuration

Current settings (adjustable in code):
- `max_attempts`: 60
- `retry_interval`: 5 seconds
- **Total timeout**: 5 minutes (300 seconds)
- **Progress updates**: Every 30 seconds

To change timeout duration, edit in `src/export/exporter.py`:
```python
max_attempts = 60      # Number of attempts
retry_interval = 5     # Seconds between attempts
# Total time = max_attempts × retry_interval
```

## Code Locations

1. **Main retry logic**: `src/export/exporter.py:195-246`
2. **User messaging**: `main.py:62-75`
3. **Error handling**: `main.py:93-103`
