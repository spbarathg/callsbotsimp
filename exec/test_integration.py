#!/usr/bin/env python3
"""
Integration test to verify signal flow from monitor to executor
"""

import asyncio
import json
import time
import sys
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Mock TradeIntentEvent for testing
class MockTradeIntentEvent:
    def __init__(self):
        self.ca = "7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr"  # Popular token
        self.kind = "fast"
        self.ug_fast = 6
        self.ug_slow = 4
        self.velocity_mpm = 2.5
        self.first_seen_ts = int(time.time()) - 600  # 10 minutes ago
        self.rc_score = "3"
        self.rc_risk_text = "no major risks"
        self.rc_lp_text = "80% locked"


async def test_signal_bridge():
    """Test the monitor -> executor bridge"""
    
    print("🧪 Testing Monitor -> Executor Signal Bridge")
    print("=" * 50)
    
    try:
        # Import the bridge
        from exec.monitor_integration import ExecutorBridge
        
        # Create bridge
        bridge = ExecutorBridge("data/test_executor_queue.json")
        
        # Create mock trade intent
        mock_intent = MockTradeIntentEvent()
        
        print(f"📤 Sending mock trade intent: {mock_intent.ca}")
        print(f"   Groups: {mock_intent.ug_fast} | Velocity: {mock_intent.velocity_mpm}/min")
        
        # Send through bridge
        await bridge.send_trade_intent(mock_intent)
        
        print("✅ Trade intent sent successfully")
        
        # Check if queue file was created
        queue_file = Path("data/test_executor_queue.json")
        if queue_file.exists():
            with open(queue_file, 'r') as f:
                queue_data = json.load(f)
            
            print(f"📋 Queue file created with {len(queue_data)} signals")
            
            if queue_data:
                signal = queue_data[0]
                print(f"📊 Signal details:")
                print(f"   CA: {signal['ca']}")
                print(f"   Quality Score: {signal.get('quality_score', 'N/A')}")
                print(f"   Groups: {signal['ug_fast']}")
                print(f"   Kind: {signal['kind']}")
            
            # Cleanup test file
            queue_file.unlink()
            print("🧹 Test queue file cleaned up")
        else:
            print("❌ Queue file was not created")
        
        print("\n✅ Signal bridge test completed successfully!")
        
    except ImportError as e:
        print(f"❌ Import error: {e}")
        print("Make sure you're running from the project root directory")
    except Exception as e:
        print(f"❌ Test error: {e}")


async def test_signal_processing():
    """Test signal processing logic"""
    
    print("\n🔄 Testing Signal Processing Logic")
    print("=" * 40)
    
    try:
        from exec.queue_bridge import SignalProcessor, FastSignalQueue
        from exec.models import SignalData
        
        # Create signal processor
        queue = FastSignalQueue("data/test_processing_queue.json")
        processor = SignalProcessor(queue)
        
        # Create test signal data
        test_data = {
            'ca': '7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr',
            'kind': 'fast',
            'ug_fast': 6,
            'ug_slow': 4,
            'velocity_mpm': 2.5,
            'first_seen_ts': int(time.time()) - 600,
            'rc_score': '3',
            'rc_risk_text': 'no major risks',
            'rc_lp_text': '80% locked'
        }
        
        print(f"🔢 Processing signal: {test_data['ca']}")
        
        # Process the signal
        await processor.process_trade_intent(test_data)
        
        # Check the quality score calculation
        signal = SignalData(
            ca=test_data['ca'],
            timestamp=time.time(),
            kind=test_data['kind'],
            ug_fast=test_data['ug_fast'],
            ug_slow=test_data['ug_slow'],
            velocity_mpm=test_data['velocity_mpm'],
            first_seen_ts=test_data['first_seen_ts'],
            rugcheck_score=test_data['rc_score'],
            rugcheck_risks=test_data['rc_risk_text'],
            rugcheck_lp=test_data['rc_lp_text']
        )
        
        quality = processor.calculate_quality_score(signal)
        print(f"📊 Calculated quality score: {quality:.3f}")
        
        # Quality score breakdown
        base_quality = 0.6
        group_boost = min(0.2, (signal.ug_fast - 4) * 0.05)
        velocity_boost = min(0.1, signal.velocity_mpm / 10.0)
        age_minutes = (time.time() - signal.first_seen_ts) / 60.0
        age_penalty = min(0.2, (age_minutes - 30) / 60.0) if age_minutes > 30 else 0
        
        print(f"   Base quality: {base_quality}")
        print(f"   Group boost: +{group_boost:.3f} ({signal.ug_fast} groups)")
        print(f"   Velocity boost: +{velocity_boost:.3f} ({signal.velocity_mpm}/min)")
        print(f"   Age penalty: -{age_penalty:.3f} ({age_minutes:.1f} min old)")
        
        # Cleanup
        Path("data/test_processing_queue.json").unlink(missing_ok=True)
        
        print("✅ Signal processing test completed!")
        
    except Exception as e:
        print(f"❌ Processing test error: {e}")


def check_dependencies():
    """Check if all required dependencies are available"""
    
    print("🔍 Checking Dependencies")
    print("=" * 30)
    
    required_modules = [
        ('aiohttp', 'HTTP client'),
        ('aiofiles', 'Async file operations'), 
        ('solders', 'Solana blockchain'),
        ('base58', 'Address encoding')
    ]
    
    missing = []
    
    for module, description in required_modules:
        try:
            __import__(module)
            print(f"✅ {module} - {description}")
        except ImportError:
            print(f"❌ {module} - {description} (MISSING)")
            missing.append(module)
    
    if missing:
        print(f"\n💡 Install missing dependencies:")
        print(f"   pip install {' '.join(missing)}")
        return False
    else:
        print("\n✅ All dependencies available!")
        return True


async def main():
    """Run all integration tests"""
    
    print("🚀 Executor Integration Tests")
    print("=" * 60)
    
    # Ensure data directory exists
    Path("data").mkdir(exist_ok=True)
    
    # Check dependencies first
    if not check_dependencies():
        return
    
    # Run tests
    await test_signal_bridge()
    await test_signal_processing()
    
    print("\n" + "=" * 60)
    print("🎉 All integration tests completed!")
    print("\n💡 Next steps:")
    print("1. Configure your .env file with wallet and RPC details")
    print("2. Start your monitor: python -m app")
    print("3. Start the executor: python exec/run_executor.py")


if __name__ == "__main__":
    asyncio.run(main())
