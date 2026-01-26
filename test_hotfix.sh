#!/bin/bash
# Quick test script for performance regression fix

echo "🔬 Testing TOBS Performance After Hotfix"
echo "========================================"
echo ""

# Check if fix was applied
if grep -q "# 🔧 HOTPATH FIX 1" src/export/exporter.py; then
    echo "✅ Hotfix detected in code"
else
    echo "❌ Hotfix NOT applied!"
    exit 1
fi

echo ""
echo "Expected improvements:"
echo "  • Speed: 536 msg/s → 750+ msg/s (+40%)"
echo "  • API time: 904s → ~630s (-30%)"
echo "  • Throughput back to ~765 msg/s baseline"
echo ""
echo "Starting export..."
echo ""

python main.py

echo ""
echo "🎯 Compare results with expected:"
echo "  ⏱️  API время должно быть ~630s (не 904s)"
echo "  ⚡ Скорость должна быть 750+ msg/s (не 536)"
