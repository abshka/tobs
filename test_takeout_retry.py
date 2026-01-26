#!/usr/bin/env python3
"""
Quick test script to verify Takeout approval waiting logic.
"""
import asyncio

# Simulate the retry logic
async def test_takeout_retry():
    max_attempts = 60
    retry_interval = 5
    
    print("=" * 70)
    print("TAKEOUT RETRY LOGIC TEST")
    print("=" * 70)
    print(f"Max attempts: {max_attempts}")
    print(f"Retry interval: {retry_interval}s")
    print(f"Total timeout: {max_attempts * retry_interval}s ({max_attempts * retry_interval // 60} minutes)")
    print(f"Progress updates: Every {retry_interval * 6}s")
    print("=" * 70)
    
    for attempt in range(max_attempts):
        print(f"\n[Attempt {attempt + 1}/{max_attempts}]", end="")
        
        # Simulate TakeoutInitDelayError on all attempts (user hasn't approved)
        if attempt == 0:
            print(f" → First attempt, requesting Takeout...")
            print(f"   ⏳ Waiting up to {max_attempts * retry_interval // 60} minutes for approval...")
        else:
            # Show progress every 30 seconds (6 attempts)
            if attempt % 6 == 0:
                elapsed = attempt * retry_interval
                remaining = (max_attempts - attempt) * retry_interval
                print(f" → ⏳ Still waiting... ({elapsed}s elapsed, {remaining}s remaining)")
            else:
                print(" → Retrying...")
        
        if attempt < max_attempts - 1:
            # In real code: await asyncio.sleep(retry_interval)
            print(f"   Sleeping {retry_interval}s before next attempt...")
            # Don't actually sleep in test
            # await asyncio.sleep(0.1)
        else:
            print(" → ❌ TIMEOUT! All attempts exhausted.")
            print(f"\n{'=' * 70}")
            print("ERROR: Takeout confirmation timeout after 5 minutes")
            print(f"{'=' * 70}")
            break
    
    print("\n✅ Test completed")

if __name__ == "__main__":
    print("This is a SIMULATION of the retry logic.")
    print("In real execution, each attempt waits 5 seconds.\n")
    
    # Run simulation
    asyncio.run(test_takeout_retry())
    
    print("\n" + "=" * 70)
    print("EXPECTED BEHAVIOR IN REAL EXPORT:")
    print("=" * 70)
    print("1. User runs: python -m tobs export")
    print("2. System requests Takeout and waits")
    print("3. User opens Telegram → Service Notifications → Approves")
    print("4. Next retry (within 5s) succeeds")
    print("5. Export starts normally")
    print("=" * 70)
