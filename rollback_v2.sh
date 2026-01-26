#!/bin/bash
# Rollback v2 changes (keep only v1)

echo "🔄 Rolling back v2 forum export fixes..."

git checkout HEAD -- src/export/exporter.py

echo "✅ Rolled back to v1 (regular export fixes only)"
echo ""
echo "Changes kept:"
echo "  • Import outside loop (v1)"
echo "  • BloomFilter optimization (v1)"  
echo "  • API timing fix for regular export (v1)"
echo ""
echo "Changes removed:"
echo "  • Forum export timing fix (v2)"
