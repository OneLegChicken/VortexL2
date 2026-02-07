#!/usr/bin/env python3
"""
VortexL2 Health Monitor

Monitors tunnel health and port forward status.
Automatically restarts failed services and recovers from disconnections.
"""

import subprocess
import logging
import re
from typing import Dict, Tuple, List, Optional
from dataclasses import dataclass
from datetime import datetime, timedelta


logger = logging.getLogger(__name__)


@dataclass
class HealthStatus:
    """Health status of a component."""
    healthy: bool
    message: str
    last_check: datetime
    failure_count: int = 0


class HealthMonitor:
    """Monitors tunnel and port forward health."""
    
    def __init__(self, check_interval_seconds: int = 30, failure_threshold: int = 3):
        """
        Initialize health monitor.
        
        Args:
            check_interval_seconds: Interval between health checks
            failure_threshold: Number of consecutive failures before recovery action
        """
        self.check_interval = check_interval_seconds
        self.failure_threshold = failure_threshold
        self.tunnel_health: Dict[str, HealthStatus] = {}
        self.port_health: Dict[int, HealthStatus] = {}
        self.last_comprehensive_check = None
    
    def run_command(self, cmd: str) -> Tuple[bool, str, str]:
        """Execute a shell command safely."""
        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=10
            )
            return result.returncode == 0, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return False, "", "Command timed out"
        except Exception as e:
            return False, "", str(e)
    
    def check_tunnel_interface_up(self, interface_name: str) -> bool:
        """Check if tunnel interface is up and has IP."""
        success, stdout, _ = self.run_command(f"ip link show {interface_name}")
        if not success:
            return False
        
        # Check if interface is UP
        if "UP" not in stdout:
            return False
        
        # Check if it has an IP address
        success, stdout, _ = self.run_command(f"ip addr show {interface_name}")
        return success and "inet " in stdout
    
    def check_tunnel_connectivity(self, tunnel_id: int) -> bool:
        """Check if L2TP tunnel has active sessions."""
        success, stdout, _ = self.run_command("ip l2tp show tunnel")
        if not success:
            return False
        
        # Look for the tunnel with active sessions
        pattern = rf"Tunnel\s+{tunnel_id}.*?\n.*?From|Tunnel\s+{tunnel_id}.*active"
        return bool(re.search(pattern, stdout, re.DOTALL))
    
    def check_port_listening(self, port: int) -> bool:
        """Check if a port is actively listening."""
        try:
            # Try multiple methods for robustness
            methods = [
                f"ss -tlnp 2>/dev/null | grep -E ':{port}\\b'",
                f"netstat -tlnp 2>/dev/null | grep -E ':{port}\\b'",
            ]
            
            for cmd in methods:
                success, _, _ = self.run_command(cmd)
                if success:
                    return True
            
            return False
        except Exception:
            return False
    
    def check_process_running(self, process_name: str) -> bool:
        """Check if a process is running."""
        success, stdout, _ = self.run_command(f"pgrep -f '{process_name}'")
        return success and bool(stdout.strip())
    
    def get_tunnel_status(self, tunnel_name: str, tunnel_id: int, interface_name: str) -> HealthStatus:
        """Get comprehensive tunnel health status."""
        checks = {
            "interface_up": self.check_tunnel_interface_up(interface_name),
            "tunnel_active": self.check_tunnel_connectivity(tunnel_id),
        }
        
        healthy = all(checks.values())
        message = f"Interface: {'UP' if checks['interface_up'] else 'DOWN'}, Tunnel: {'Active' if checks['tunnel_active'] else 'Inactive'}"
        
        status = HealthStatus(
            healthy=healthy,
            message=message,
            last_check=datetime.now()
        )
        
        return status
    
    def get_port_forward_status(self, port: int) -> HealthStatus:
        """Get port forward health status."""
        listening = self.check_port_listening(port)
        
        message = f"Port {port}: {'LISTENING' if listening else 'NOT LISTENING'}"
        
        status = HealthStatus(
            healthy=listening,
            message=message,
            last_check=datetime.now()
        )
        
        return status
    
    def check_all_tunnel_health(self, tunnels: List) -> Dict[str, HealthStatus]:
        """Check health of all tunnels."""
        results = {}
        
        for tunnel_config in tunnels:
            tunnel_name = tunnel_config.name
            status = self.get_tunnel_status(
                tunnel_name,
                tunnel_config.tunnel_id,
                tunnel_config.interface_name
            )
            
            # Track failure count
            if tunnel_name in self.tunnel_health:
                old_status = self.tunnel_health[tunnel_name]
                if not status.healthy and not old_status.healthy:
                    status.failure_count = old_status.failure_count + 1
                elif status.healthy:
                    status.failure_count = 0
            else:
                status.failure_count = 0 if status.healthy else 1
            
            self.tunnel_health[tunnel_name] = status
            results[tunnel_name] = status
            
            # Log status
            level = logging.INFO if status.healthy else logging.WARNING
            logger.log(level, f"Tunnel '{tunnel_name}': {status.message} (failures: {status.failure_count})")
        
        return results
    
    def check_all_port_health(self, ports: List[int]) -> Dict[int, HealthStatus]:
        """Check health of all port forwards."""
        results = {}
        
        for port in ports:
            status = self.get_port_forward_status(port)
            
            # Track failure count
            if port in self.port_health:
                old_status = self.port_health[port]
                if not status.healthy and not old_status.healthy:
                    status.failure_count = old_status.failure_count + 1
                elif status.healthy:
                    status.failure_count = 0
            else:
                status.failure_count = 0 if status.healthy else 1
            
            self.port_health[port] = status
            results[port] = status
            
            # Log status
            level = logging.INFO if status.healthy else logging.WARNING
            logger.log(level, f"Port {port}: {status.message} (failures: {status.failure_count})")
        
        return results
    
    def should_attempt_recovery(self, status: HealthStatus) -> bool:
        """Determine if recovery should be attempted."""
        if status.healthy:
            return False
        return status.failure_count >= self.failure_threshold
    
    def get_unhealthy_components(self) -> Tuple[List[str], List[int]]:
        """Get list of unhealthy tunnels and ports."""
        unhealthy_tunnels = [
            name for name, status in self.tunnel_health.items() 
            if not status.healthy
        ]
        
        unhealthy_ports = [
            port for port, status in self.port_health.items() 
            if not status.healthy
        ]
        
        return unhealthy_tunnels, unhealthy_ports
    
    def get_recovery_needed(self) -> Tuple[List[str], List[int]]:
        """Get components that need recovery."""
        recovery_tunnels = [
            name for name, status in self.tunnel_health.items() 
            if self.should_attempt_recovery(status)
        ]
        
        recovery_ports = [
            port for port, status in self.port_health.items() 
            if self.should_attempt_recovery(status)
        ]
        
        return recovery_tunnels, recovery_ports
    
    def clear_port_health(self, port: int) -> None:
        """Clear health status for a port (when removing)."""
        if port in self.port_health:
            del self.port_health[port]
    
    def print_health_report(self) -> str:
        """Generate a readable health report."""
        lines = ["=== VortexL2 Health Report ===\n"]
        
        if self.tunnel_health:
            lines.append("TUNNELS:")
            for name, status in self.tunnel_health.items():
                status_icon = "✓" if status.healthy else "✗"
                lines.append(f"  {status_icon} {name}: {status.message}")
        
        if self.port_health:
            lines.append("\nPORTS:")
            for port, status in self.port_health.items():
                status_icon = "✓" if status.healthy else "✗"
                lines.append(f"  {status_icon} Port {port}: {status.message}")
        
        return "\n".join(lines)
