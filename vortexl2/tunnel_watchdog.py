#!/usr/bin/env python3
"""
VortexL2 Tunnel Watchdog

Monitors tunnel health and automatically recovers from failures.
Restarts failed tunnels and port forwards with backoff strategy.
"""

import asyncio
import logging
import signal
import sys
from pathlib import Path
from typing import List, Optional

# Ensure we can import the package
sys.path.insert(0, str(Path(__file__).parent.parent))

from vortexl2.config import ConfigManager, TunnelConfig
from vortexl2.tunnel import TunnelManager
from vortexl2.health_monitor import HealthMonitor


# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/var/log/vortexl2/watchdog.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class TunnelWatchdog:
    """Monitors and recovers tunnel health."""
    
    def __init__(self, check_interval: int = 30, recovery_delay: int = 5):
        """
        Initialize watchdog.
        
        Args:
            check_interval: Seconds between health checks
            recovery_delay: Seconds before attempting recovery
        """
        self.check_interval = check_interval
        self.recovery_delay = recovery_delay
        self.config_manager = ConfigManager()
        self.health_monitor = HealthMonitor(check_interval, failure_threshold=2)
        self.running = False
        self.tunnel_managers = {}
    
    async def initialize(self):
        """Initialize tunnel managers for all configured tunnels."""
        tunnels = self.config_manager.get_all_tunnels()
        
        for tunnel_config in tunnels:
            if tunnel_config.is_configured():
                self.tunnel_managers[tunnel_config.name] = TunnelManager(tunnel_config)
                logger.info(f"Initialized watchdog for tunnel: {tunnel_config.name}")
    
    async def check_health(self):
        """Check health of all tunnels and ports."""
        tunnels = self.config_manager.get_all_tunnels()
        configured_tunnels = [t for t in tunnels if t.is_configured()]
        
        # Get all ports from all tunnels
        all_ports = []
        for tunnel in configured_tunnels:
            all_ports.extend(tunnel.forwarded_ports)
        
        # Check tunnel health
        tunnel_statuses = self.health_monitor.check_all_tunnel_health(configured_tunnels)
        
        # Check port health
        port_statuses = self.health_monitor.check_all_port_health(all_ports)
        
        return tunnel_statuses, port_statuses
    
    async def recover_unhealthy_tunnel(self, tunnel_config: TunnelConfig):
        """Attempt to recover an unhealthy tunnel."""
        tunnel_name = tunnel_config.name
        logger.warning(f"Attempting to recover tunnel: {tunnel_name}")
        
        if tunnel_name not in self.tunnel_managers:
            self.tunnel_managers[tunnel_name] = TunnelManager(tunnel_config)
        
        tunnel_mgr = self.tunnel_managers[tunnel_name]
        
        try:
            # Try to delete and recreate tunnel
            success, msg = tunnel_mgr.delete_tunnel()
            if not success and "does not exist" not in msg:
                logger.warning(f"Failed to delete tunnel: {msg}")
            
            # Wait before recreating
            await asyncio.sleep(self.recovery_delay)
            
            # Recreate tunnel
            success, msg = tunnel_mgr.full_setup()
            if success:
                logger.info(f"Successfully recovered tunnel: {tunnel_name}")
                return True
            else:
                logger.error(f"Failed to recover tunnel {tunnel_name}: {msg}")
                return False
        except Exception as e:
            logger.error(f"Exception during tunnel recovery: {e}")
            return False
    
    async def recover_unhealthy_ports(self, tunnel_config: TunnelConfig):
        """Attempt to restart unhealthy ports for a tunnel."""
        tunnel_name = tunnel_config.name
        unhealthy_ports = [
            port for port in tunnel_config.forwarded_ports
            if port in self.health_monitor.port_health and 
               not self.health_monitor.port_health[port].healthy
        ]
        
        if not unhealthy_ports:
            return True
        
        logger.warning(f"Attempting to restart {len(unhealthy_ports)} unhealthy ports in tunnel {tunnel_name}")
        
        try:
            # We need to get the forward manager for this tunnel
            from vortexl2.forward import get_forward_manager
            forward_manager = get_forward_manager(tunnel_config)
            
            if not forward_manager:
                logger.error(f"Could not get forward manager for tunnel {tunnel_name}")
                return False
            
            # Try to restart each unhealthy port
            recovered = 0
            for port in unhealthy_ports:
                try:
                    # Try to remove and re-add the port
                    success, msg = forward_manager.remove_forward(port)
                    if not success and "not in forwarded list" not in msg:
                        logger.warning(f"Failed to remove port {port}: {msg}")
                    
                    await asyncio.sleep(1)
                    
                    success, msg = forward_manager.create_forward(port)
                    if success:
                        logger.info(f"Successfully recovered port {port}: {msg}")
                        recovered += 1
                        self.health_monitor.clear_port_health(port)
                    else:
                        logger.error(f"Failed to recover port {port}: {msg}")
                except Exception as e:
                    logger.error(f"Exception recovering port {port}: {e}")
            
            return recovered > 0
        
        except Exception as e:
            logger.error(f"Exception during port recovery: {e}")
            return False
    
    async def recovery_cycle(self):
        """Perform recovery for unhealthy components."""
        tunnels = self.config_manager.get_all_tunnels()
        unhealthy_tunnels, unhealthy_ports = self.health_monitor.get_recovery_needed()
        
        # Recover tunnels first
        for tunnel_name in unhealthy_tunnels:
            tunnel_config = next((t for t in tunnels if t.name == tunnel_name), None)
            if tunnel_config:
                await self.recover_unhealthy_tunnel(tunnel_config)
                await asyncio.sleep(2)  # Wait between recoveries
        
        # Then recover ports
        for tunnel in tunnels:
            if tunnel.is_configured():
                await self.recover_unhealthy_ports(tunnel)
    
    async def run(self):
        """Main watchdog loop."""
        logger.info("Starting VortexL2 Tunnel Watchdog")
        
        await self.initialize()
        
        self.running = True
        
        while self.running:
            try:
                # Check health
                tunnel_statuses, port_statuses = await self.check_health()
                
                # Log health summary
                if tunnel_statuses or port_statuses:
                    logger.debug(self.health_monitor.print_health_report())
                
                # Attempt recovery if needed
                tunnels_to_recover, ports_to_recover = self.health_monitor.get_recovery_needed()
                if tunnels_to_recover or ports_to_recover:
                    logger.warning(f"Recovery needed - Tunnels: {tunnels_to_recover}, Ports: {ports_to_recover}")
                    await asyncio.sleep(2)  # Brief delay before recovery
                    await self.recovery_cycle()
                
                # Wait before next check
                await asyncio.sleep(self.check_interval)
            
            except Exception as e:
                logger.error(f"Error in watchdog loop: {e}")
                await asyncio.sleep(5)  # Wait before retrying
    
    async def stop(self):
        """Stop the watchdog."""
        logger.info("Stopping VortexL2 Tunnel Watchdog")
        self.running = False


async def main():
    """Main entry point."""
    watchdog = TunnelWatchdog(check_interval=30, recovery_delay=5)
    
    # Setup signal handlers
    def handle_signal(sig, frame):
        logger.info(f"Received signal {sig}")
        asyncio.create_task(watchdog.stop())
    
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    
    try:
        await watchdog.run()
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        await watchdog.stop()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
