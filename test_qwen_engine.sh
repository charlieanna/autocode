#!/bin/bash
# Test script for Qwen engine integration
# Run this in WSL after installing Python 3.11+

set -e

echo "=== Qwen Engine Test Script ==="
echo ""

# Check if we're in WSL
if grep -q Microsoft /proc/version 2>/dev/null; then
    echo "✓ Running in WSL"
else
    echo "⚠ Not running in WSL (may still work on Linux/macOS)"
fi

# Check Python version
echo ""
echo "Checking Python version..."
python3 --version || { echo "❌ Python 3 not found. Install with: sudo apt install python3"; exit 1; }

# Check if qwen CLI is available
echo ""
echo "Checking Qwen CLI..."
if command -v qwen &> /dev/null; then
    echo "✓ Qwen CLI found"
    qwen --version
else
    echo "❌ Qwen CLI not found in PATH"
    echo "   Install Qwen Code CLI first"
    exit 1
fi

# Navigate to autocode directory
cd "$(dirname "$0")"
echo ""
echo "Working directory: $(pwd)"

# Test 1: Dry run with Qwen engine
echo ""
echo "=== Test 1: Dry run with Qwen engine ==="
python3 tools/autocode.py --engine qwen --dry-run "build a simple hello world API" || {
    echo "❌ Dry run failed"
    exit 1
}
echo "✓ Dry run succeeded"

# Test 2: Check that Qwen models are recognized
echo ""
echo "=== Test 2: Verify Qwen model configuration ==="
python3 -c "
import sys
sys.path.insert(0, 'tools')
import autocode_qwen as qwen

# Check default models
print('Default Qwen models:')
for role, model in qwen.DEFAULT_MODELS.items():
    print(f'  {role}: {model}')

# Check model validation against the transport's own defaults
test_roles = {role: {'model': model} for role, model in qwen.DEFAULT_MODELS.items()}
try:
    qwen.check_models(test_roles)
    print('✓ Model validation passed')
except Exception as e:
    print(f'❌ Model validation failed: {e}')
    sys.exit(1)

# A default run must not pause with PAUSED_CROSS_MODEL before it launches.
for producer, verifier in (('glm', 'plan_reviewer'), ('terra', 'sol'), ('terra', 'completion')):
    if qwen.DEFAULT_MODELS[producer] == qwen.DEFAULT_MODELS[verifier]:
        print(f'❌ {producer} and {verifier} share one model')
        sys.exit(1)
print('✓ Verifier roles are independent by default')
" || {
    echo "❌ Model configuration test failed"
    exit 1
}

# Test 3: Verify engine is registered
echo ""
echo "=== Test 3: Verify Qwen engine is registered ==="
python3 -c "
import sys
sys.path.insert(0, 'tools')
import autocode_args as args

parser = args.build_parser()
# Check if 'qwen' is in the engine choices
engine_action = None
for action in parser._actions:
    if hasattr(action, 'dest') and action.dest == 'engine':
        engine_action = action
        break

if engine_action and 'qwen' in engine_action.choices:
    print('✓ Qwen engine is registered in CLI arguments')
else:
    print('❌ Qwen engine not found in CLI arguments')
    sys.exit(1)
" || {
    echo "❌ Engine registration test failed"
    exit 1
}

echo ""
echo "=== Test 4: Transport and run-configuration unit tests ==="
python3 tools/test_qwen.py || {
    echo "❌ Transport tests failed"
    exit 1
}
python3 -m unittest tests.test_qwen_engine || {
    echo "❌ Run-configuration tests failed"
    exit 1
}
echo "✓ Unit tests passed"

echo ""
echo "=== All tests passed! ==="
echo ""
echo "You can now use autocode with Qwen engine:"
echo "  python3 tools/autocode.py --engine qwen \"your task here\""
echo ""
echo "Or with specific models:"
echo "  python3 tools/autocode.py --engine qwen --terra-model qwen/qwen3.7-plus \"your task\""
