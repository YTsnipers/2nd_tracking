import os
import asyncio
import logging
import json
import time
from datetime import datetime
from typing import Dict, List, Set
import aiohttp
from web3 import Web3
from web3.exceptions import BlockNotFound, TransactionNotFound
import telegram
from telegram.ext import Application
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('monitor.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class EthereumMonitor:
    def __init__(self):
        # Environment variables configuration
        self.eth_api_url = os.getenv('ETH_API_URL')  # Infura/Alchemy API URL
        self.telegram_token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.telegram_chat_id = os.getenv('TELEGRAM_CHAT_ID')
        
        # Validate required environment variables
        if not all([self.eth_api_url, self.telegram_token, self.telegram_chat_id]):
            missing = []
            if not self.eth_api_url: missing.append('ETH_API_URL')
            if not self.telegram_token: missing.append('TELEGRAM_BOT_TOKEN')
            if not self.telegram_chat_id: missing.append('TELEGRAM_CHAT_ID')
            
            logger.error(f"Missing required environment variables: {missing}")
            logger.error("Please ensure .env file contains all necessary configurations")
            raise ValueError(f"Missing required environment variables: {missing}")
        
        # Monitored addresses configuration
        self.monitored_addresses = {
            '0x231FC5b039d66BA234CB90357082Bf16Be79B17c': 'Pendle_airdrop1',
            '0x8270400d528c34e1596EF367eeDEc99080A1b592': 'Pendle_airdrop2'
        }
        
        # Web3 initialization
        try:
            self.w3 = Web3(Web3.HTTPProvider(self.eth_api_url))
            if not self.w3.is_connected():
                raise ConnectionError("Unable to connect to Ethereum node")
        except Exception as e:
            logger.error(f"Web3 initialization failed: {e}")
            raise
        
        # Telegram Bot initialization
        try:
            self.bot = telegram.Bot(token=self.telegram_token)
        except Exception as e:
            logger.error(f"Telegram Bot initialization failed: {e}")
            raise
        
        # State management
        self.last_processed_block = self.get_last_processed_block()
        self.processed_txns = set()  # Avoid duplicate processing
        
        # Monitoring interval (seconds)
        self.poll_interval = int(os.getenv('POLL_INTERVAL', 70))
        
        logger.info("Ethereum monitoring system initialized successfully")
        logger.info(f"Monitored addresses: {list(self.monitored_addresses.keys())}")
        logger.info(f"Current block: {self.w3.eth.block_number}")

    def get_last_processed_block(self) -> int:
        """Get the last processed block number"""
        try:
            with open('last_block.txt', 'r') as f:
                block_num = int(f.read().strip())
                logger.info(f"Loaded last processed block from file: {block_num}")
                return block_num
        except (FileNotFoundError, ValueError):
            # If file doesn't exist, start from current block
            current_block = self.w3.eth.block_number
            logger.info(f"Initialization: Starting monitoring from block {current_block}")
            return current_block

    def save_last_processed_block(self, block_number: int):
        """Save the last processed block number"""
        with open('last_block.txt', 'w') as f:
            f.write(str(block_number))

    def is_external_transaction(self, tx: Dict) -> bool:
        """
        Determine if this is an external transaction
        External transaction characteristics:
        1. tx['input'] is '0x' indicates simple transfer
        2. or from address is EOA (Externally Owned Account)
        """
        try:
            # Check if from address is a contract
            from_code = self.w3.eth.get_code(tx['from'])
            
            # If from address has code, it's a contract address, skip
            if len(from_code) > 0:
                logger.debug(f"Skipping contract transaction: {tx['hash'].hex()}")
                return False
                
            # Check if it's direct transfer or contract call (from EOA)
            return True
            
        except Exception as e:
            logger.error(f"Error checking transaction type: {e}")
            return False

    def format_transaction_message(self, tx: Dict, address_label: str, is_incoming: bool) -> str:
        """Format transaction message in English"""
        direction = "Incoming" if is_incoming else "Outgoing"
        value_eth = self.w3.from_wei(tx['value'], 'ether')
        
        message = f"""
🔔 **Pendle Airdrop Address Activity Alert**

📍 **Monitored Address**: {address_label}
🔄 **Direction**: {direction}
💰 **Amount**: {value_eth:.6f} ETH
🏷️ **Tx Hash**: `{tx['hash'].hex()}`
👤 **From**: `{tx['from']}`
👤 **To**: `{tx['to']}`
⛽ **Gas Price**: {self.w3.from_wei(tx['gasPrice'], 'gwei'):.2f} Gwei
🔢 **Block**: {tx['blockNumber']}
⏰ **Time**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

🔗 [View on Etherscan](https://etherscan.io/tx/{tx['hash'].hex()})
        """
        return message.strip()

    async def send_telegram_notification(self, message: str):
        """Send Telegram notification"""
        try:
            await self.bot.send_message(
                chat_id=self.telegram_chat_id,
                text=message,
                parse_mode='Markdown',
                disable_web_page_preview=True
            )
            logger.info("Telegram notification sent successfully")
        except Exception as e:
            logger.error(f"Failed to send Telegram notification: {e}")

    async def process_transaction(self, tx: Dict):
        """Process single transaction"""
        tx_hash = tx['hash'].hex()
        
        # Avoid duplicate processing
        if tx_hash in self.processed_txns:
            return
            
        self.processed_txns.add(tx_hash)
        
        # Check if it's an external transaction
        if not self.is_external_transaction(tx):
            return
        
        # Check if transaction involves monitored addresses
        from_addr = tx['from'].lower() if tx['from'] else None
        to_addr = tx['to'].lower() if tx['to'] else None
        
        for monitored_addr, label in self.monitored_addresses.items():
            monitored_addr_lower = monitored_addr.lower()
            
            # Check if it's an outgoing transaction
            if from_addr == monitored_addr_lower:
                logger.info(f"🚨 Detected {label} outgoing transaction: {tx_hash}")
                message = self.format_transaction_message(tx, label, False)
                await self.send_telegram_notification(message)
            
            # Check if it's an incoming transaction
            elif to_addr == monitored_addr_lower:
                logger.info(f"🚨 Detected {label} incoming transaction: {tx_hash}")
                message = self.format_transaction_message(tx, label, True)
                await self.send_telegram_notification(message)

    async def scan_block(self, block_number: int):
        """Scan single block"""
        try:
            block = self.w3.eth.get_block(block_number, full_transactions=True)
            logger.debug(f"Scanning block {block_number}, transactions: {len(block.transactions)}")
            
            for tx in block.transactions:
                await self.process_transaction(tx)
                
        except BlockNotFound:
            logger.warning(f"Block {block_number} not found, may not be confirmed yet")
        except Exception as e:
            logger.error(f"Error scanning block {block_number}: {e}")

    async def monitor_loop(self):
        """Main monitoring loop"""
        logger.info("Starting monitoring loop...")
        
        while True:
            try:
                current_block = self.w3.eth.block_number
                
                # Process new blocks
                blocks_to_process = current_block - self.last_processed_block
                if blocks_to_process > 0:
                    logger.info(f"Need to process {blocks_to_process} new blocks")
                    
                    while self.last_processed_block < current_block:
                        self.last_processed_block += 1
                        await self.scan_block(self.last_processed_block)
                        self.save_last_processed_block(self.last_processed_block)
                
                # Clean old processed records (keep latest 1000)
                if len(self.processed_txns) > 1000:
                    self.processed_txns = set(list(self.processed_txns)[-500:])
                
                logger.debug(f"Current block: {current_block}, Last processed block: {self.last_processed_block}")
                
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
                await asyncio.sleep(5)  # Short rest after error
            
            await asyncio.sleep(self.poll_interval)

    async def test_connections(self):
        """Test connections"""
        logger.info("Starting connection test...")
        
        # Test Ethereum connection
        try:
            current_block = self.w3.eth.block_number
            logger.info(f"✅ Ethereum connection successful, current block: {current_block}")
        except Exception as e:
            logger.error(f"❌ Ethereum connection failed: {e}")
            return False
        
        # Test Telegram connection
        try:
            test_message = f"""
🧪 **Monitoring System Connection Test**

✅ Ethereum Connection Success
✅ Telegram Connection Success
📊 Current Block: {current_block}
⏰ Test Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

Monitored Addresses:
- {self.monitored_addresses['0x231FC5b039d66BA234CB90357082Bf16Be79B17c']}
- {self.monitored_addresses['0x8270400d528c34e1596EF367eeDEc99080A1b592']}
            """.strip()
            
            await self.send_telegram_notification(test_message)
            logger.info("✅ Telegram connection test successful")
            return True
        except Exception as e:
            logger.error(f"❌ Telegram connection test failed: {e}")
            return False

    async def start_monitoring(self):
        """Start monitoring"""
        try:
            # Test connections
            if not await self.test_connections():
                logger.error("Connection test failed, please check configuration")
                return
            
            # Send startup notification
            start_message = f"""
🚀 **Ethereum Address Monitoring System Started**

Monitored Addresses:
- **{self.monitored_addresses['0x231FC5b039d66BA234CB90357082Bf16Be79B17c']}**: `0x231FC...B17c`
- **{self.monitored_addresses['0x8270400d528c34e1596EF367eeDEc99080A1b592']}**: `0x8270...b592`

⚙️ Configuration:
- Polling Interval: {self.poll_interval}s
- Starting Block: {self.last_processed_block}

✅ System started, monitoring in real-time...
⏰ Start Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
            """.strip()
            
            await self.send_telegram_notification(start_message)
            
            # Start monitoring loop
            await self.monitor_loop()
            
        except KeyboardInterrupt:
            logger.info("Received stop signal, shutting down monitoring system...")
            # Send stop notification
            stop_message = "⏹️ Monitoring system has stopped running"
            try:
                await self.send_telegram_notification(stop_message)
            except:
                pass
        except Exception as e:
            logger.error(f"Fatal error in monitoring system: {e}")
            # Send error notification
            error_message = f"❌ Monitoring system error: {str(e)}"
            try:
                await self.send_telegram_notification(error_message)
            except:
                pass
            raise

def main():
    """Main function"""
    try:
        # Create and start monitor
        monitor = EthereumMonitor()
        asyncio.run(monitor.start_monitoring())
    except KeyboardInterrupt:
        logger.info("Program interrupted by user")
    except Exception as e:
        logger.error(f"Program exited with error: {e}")

if __name__ == "__main__":
    main()
