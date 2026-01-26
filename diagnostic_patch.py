"""
Enhanced API profiling для диагностики регрессии.

Добавьте это в src/telegram_client.py в метод get_topic_messages_stream
после строки 1237 (batch_size = getattr(...))
"""

# DIAGNOSTIC: Add detailed timing
import time as time_module
batch_fetch_times = []
batch_start = time_module.time()

# Внутри while True loop, перед await self.client.get_messages:
api_call_start = time_module.time()

# После await self.client.get_messages:
api_call_end = time_module.time()
batch_fetch_times.append(api_call_end - api_call_start)

# В конце метода (перед return/break):
if batch_fetch_times:
    avg_fetch = sum(batch_fetch_times) / len(batch_fetch_times)
    logger.info(
        f"🔍 DIAGNOSTIC: Topic {topic_id} - "
        f"{len(batch_fetch_times)} fetches, "
        f"avg {avg_fetch:.2f}s per fetch, "
        f"total API time: {sum(batch_fetch_times):.2f}s"
    )
