#!/usr/bin/env python3
"""
VortexL2 TCP Performance Optimization

Tunes system TCP/IP stack and socket buffers for optimal throughput
through the tunnel with large MTU and congestion control optimization.
"""

import subprocess
import logging
from typing import Tuple, List, Dict


logger = logging.getLogger(__name__)


class TCPOptimizer:
    """Optimizes kernel parameters for tunnel throughput."""
    
    # Optimized sysctl parameters for high-throughput tunnels
    SYSCTL_PARAMS = {
        # TCP window scaling (critical for high-bandwidth)
        "net.ipv4.tcp_window_scaling": "1",
        
        # Buffer sizes (in bytes) - increased for high throughput
        "net.core.rmem_max": "134217728",      # 128MB
        "net.core.wmem_max": "134217728",      # 128MB
        "net.ipv4.tcp_rmem": "4096 87380 67108864",  # min, default, max
        "net.ipv4.tcp_wmem": "4096 65536 67108864",  # min, default, max
        
        # Socket backlog
        "net.core.somaxconn": "16384",
        "net.ipv4.tcp_max_syn_backlog": "16384",
        
        # TCP optimizations
        "net.ipv4.tcp_tw_reuse": "1",
        "net.ipv4.tcp_fin_timeout": "30",
        "net.ipv4.tcp_keepalive_time": "600",
        "net.ipv4.tcp_keepalive_probes": "3",
        "net.ipv4.tcp_keepalive_intvl": "10",
        
        # Congestion control - use BBR if available, else CUBIC
        "net.ipv4.tcp_congestion_control": "bbr",
        
        # Path MTU discovery
        "net.ipv4.ip_no_pmtu_disc": "0",
        
        # Enable TCP fast open for faster connections
        "net.ipv4.tcp_fastopen": "3",
        
        # Increase max connections per port
        "net.ipv4.ip_local_port_range": "1024 65535",
    }
    
    def run_command(self, cmd: str) -> Tuple[bool, str]:
        """Execute a shell command."""
        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=10
            )
            return result.returncode == 0, result.stderr if result.returncode != 0 else result.stdout
        except subprocess.TimeoutExpired:
            return False, "Command timed out"
        except Exception as e:
            return False, str(e)
    
    def get_current_bbrv2_status(self) -> bool:
        """Check if BBRv2 kernel module is loaded."""
        success, output = self.run_command("lsmod | grep tcp_bbr")
        return success and bool(output.strip())
    
    def apply_sysctl_params(self, params: Dict[str, str] = None) -> Tuple[bool, List[str]]:
        """
        Apply sysctl parameters for TCP optimization.
        
        Returns:
            (success, list of applied/failed parameters)
        """
        if params is None:
            params = self.SYSCTL_PARAMS
        
        results = []
        failed = 0
        
        for param, value in params.items():
            # Check if BBR is not available, skip it
            if param == "net.ipv4.tcp_congestion_control" and value == "bbr":
                if not self.get_current_bbrv2_status():
                    logger.info(f"BBR not available, trying CUBIC instead")
                    value = "cubic"
            
            cmd = f"sysctl -w {param}={value}"
            success, output = self.run_command(cmd)
            
            if success:
                results.append(f"✓ {param} = {value}")
                logger.debug(f"Applied: {param} = {value}")
            else:
                results.append(f"✗ {param} = {value} (Error: {output[:50]})")
                logger.warning(f"Failed to set {param}: {output}")
                failed += 1
        
        return failed == 0, results
    
    def get_current_params(self, params: List[str] = None) -> Dict[str, str]:
        """Get current values of sysctl parameters."""
        if params is None:
            params = list(self.SYSCTL_PARAMS.keys())
        
        current = {}
        for param in params:
            success, output = self.run_command(f"sysctl -n {param}")
            if success:
                current[param] = output.strip()
            else:
                current[param] = "ERROR"
        
        return current
    
    def make_persistent(self) -> Tuple[bool, str]:
        """
        Write parameters to /etc/sysctl.d/99-vortexl2.conf
        to persist across reboots.
        """
        try:
            config_content = "# VortexL2 TCP Performance Optimization\n"
            config_content += "# Applied for high-throughput L2TPv3 tunneling\n\n"
            
            for param, value in self.SYSCTL_PARAMS.items():
                config_content += f"{param} = {value}\n"
            
            with open("/etc/sysctl.d/99-vortexl2.conf", "w") as f:
                f.write(config_content)
            
            logger.info("Created /etc/sysctl.d/99-vortexl2.conf")
            return True, "Parameters saved to /etc/sysctl.d/99-vortexl2.conf"
        
        except Exception as e:
            return False, f"Failed to write sysctl config: {e}"
    
    def optimize(self) -> Tuple[bool, str]:
        """
        Apply all TCP optimizations and make them persistent.
        
        Returns:
            (success, detailed report)
        """
        report = ["=== TCP Performance Optimization ===\n"]
        
        # Check current state
        current = self.get_current_params()
        report.append("Current TCP settings:")
        for param, value in sorted(current.items())[:5]:
            report.append(f"  {param}: {value}")
        report.append("")
        
        # Apply optimizations
        success, results = self.apply_sysctl_params()
        report.append("Applying optimizations:")
        report.extend(results)
        report.append("")
        
        # Make persistent
        persist_success, persist_msg = self.make_persistent()
        report.append(f"Persistence: {persist_msg}")
        
        final_success = success and persist_success
        return final_success, "\n".join(report)
    
    def print_recommendations(self) -> str:
        """Print optimization recommendations."""
        recommendations = """
=== TCP Optimization Recommendations ===

1. BUFFER SIZES:
   - Increased to 128MB max for high-throughput scenarios
   - Allows TCP to buffer more data in transit
   - Critical for WAN links with high latency

2. WINDOW SCALING:
   - Enabled to allow TCP window sizes > 64KB
   - Improves throughput on high-bandwidth links

3. CONGESTION CONTROL:
   - Using BBR (Bottleneck Bandwidth and RTT) if available
   - Falls back to CUBIC for older kernels
   - Better for lossy networks and tunnels

4. TCP FAST OPEN:
   - Reduces 3-way handshake latency by 1 RTT
   - Improves connection establishment speed

5. TIME WAIT REUSE:
   - Reduces TIME_WAIT socket accumulation
   - Allows faster port reuse

MONITORING:
  - Check TCP stats: ss -s
  - Monitor performance: iperf3 -c <tunnel_ip> -t 30
  - Review sysctl: sysctl -a | grep tcp_
"""
        return recommendations


def setup_tcp_optimization() -> Tuple[bool, str]:
    """Convenience function to set up TCP optimization."""
    optimizer = TCPOptimizer()
    return optimizer.optimize()
