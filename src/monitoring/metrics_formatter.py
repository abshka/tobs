# -*- coding: utf-8 -*-
"""
Metrics summary formatting utilities for human-readable logging.

Part of TIER C-4: Enhanced Metrics System
"""

from typing import Dict, Any

try:
    from ..utils import logger
except Exception:
    import logging
    logger = logging.getLogger(__name__)


def format_metrics_summary(metrics_data: Dict[str, Any]) -> str:
    """
    Format metrics JSON data into human-readable summary.
    
    Args:
        metrics_data: JSON-serializable dict from MetricsCollector.export_json()
    
    Returns:
        Formatted multi-line string ready for logging
    """
    lines = []
    lines.append("=" * 70)
    lines.append("📊 TIER C-4: Export Metrics Summary")
    lines.append("=" * 70)
    
    # Stage timings
    if metrics_data.get("stages"):
        lines.append("\n🔄 Pipeline Stage Performance:")
        lines.append(f"{'Stage':<20} {'Duration':>12} {'Count':>10} {'Avg/Item':>12}")
        lines.append("-" * 70)
        
        for stage_name, stats in sorted(metrics_data["stages"].items()):
            duration = stats.get("total_duration_seconds", 0)
            count = stats.get("total_count", 0)
            avg = stats.get("avg_duration_seconds", 0)
            
            lines.append(
                f"{stage_name:<20} {duration:>10.2f}s {count:>10} {avg:>10.4f}s"
            )
        
        lines.append("-" * 70)
    
    # Resource usage
    if metrics_data.get("resources"):
        res = metrics_data["resources"]
        lines.append("\n💻 Resource Utilization:")
        lines.append(f"  • Peak CPU Usage:    {res.get('peak_cpu_percent', 0):>6.1f}%")
        lines.append(f"  • Peak Memory (RSS): {res.get('peak_memory_mb', 0):>6.1f} MB")
        lines.append(f"  • Avg CPU Usage:     {res.get('avg_cpu_percent', 0):>6.1f}%")
        lines.append(f"  • Avg Memory (RSS):  {res.get('avg_memory_mb', 0):>6.1f} MB")
        
        # Disk I/O
        disk_read = res.get('total_disk_read_mb', 0)
        disk_write = res.get('total_disk_write_mb', 0)
        if disk_read > 0 or disk_write > 0:
            lines.append(f"  • Disk Read:         {disk_read:>6.1f} MB")
            lines.append(f"  • Disk Write:        {disk_write:>6.1f} MB")
        
        # Network I/O
        net_sent = res.get('total_network_sent_mb', 0)
        net_recv = res.get('total_network_recv_mb', 0)
        if net_sent > 0 or net_recv > 0:
            lines.append(f"  • Network Sent:      {net_sent:>6.1f} MB")
            lines.append(f"  • Network Received:  {net_recv:>6.1f} MB")
        
        sample_count = res.get('sample_count', 0)
        if sample_count > 0:
            lines.append(f"  • Samples Collected: {sample_count:>6}")
    
    # Cache performance
    if metrics_data.get("caches"):
        lines.append("\n🗄️ Cache Performance (TIER C-3):")
        lines.append(f"{'Cache':<25} {'Hits':>10} {'Misses':>10} {'Hit Rate':>12}")
        lines.append("-" * 70)
        
        for cache_name, stats in sorted(metrics_data["caches"].items()):
            hits = stats.get("hits", 0)
            misses = stats.get("misses", 0)
            total = hits + misses
            hit_rate = (hits / total * 100) if total > 0 else 0.0
            
            lines.append(
                f"{cache_name:<25} {hits:>10} {misses:>10} {hit_rate:>10.1f}%"
            )
        
        lines.append("-" * 70)
    
    lines.append("=" * 70)
    
    return "\n".join(lines)


def log_metrics_summary(metrics_data: Dict[str, Any]):
    """
    Log formatted metrics summary to logger.
    
    Args:
        metrics_data: JSON-serializable dict from MetricsCollector.export_json()
    """
    summary = format_metrics_summary(metrics_data)
    
    # Log each line separately for better formatting in different log handlers
    for line in summary.split("\n"):
        logger.info(line)
