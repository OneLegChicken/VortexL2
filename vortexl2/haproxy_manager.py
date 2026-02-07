import subprocess
import logging
from pathlib import Path
from typing import List, Tuple, Dict, Optional

# Setup logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# HAProxy configuration paths
# Use default HAProxy config path so systemctl reload works
HAPROXY_CONFIG_DIR = Path("/etc/haproxy")
HAPROXY_CONFIG_FILE = HAPROXY_CONFIG_DIR / "haproxy.cfg"
HAPROXY_BACKUP_FILE = HAPROXY_CONFIG_DIR / "haproxy.cfg.bak"
HAPROXY_STATS_FILE = Path("/var/lib/vortexl2/haproxy-stats")
HAPROXY_SOCKET = Path("/var/run/haproxy.sock")

class HAProxyManager:
    """Manages HAProxy for port forwarding."""
    
    def __init__(self, config):
        self.config = config
        self.haproxy_config_path = HAPROXY_CONFIG_FILE
    
    def _generate_haproxy_config(self) -> str:
        """Generate HAProxy configuration with optimizations for stability and performance."""
        from vortexl2.config import ConfigManager
        
        config = """
# Auto-generated HAProxy config for vortexl2
global
    maxconn 32768
    log stdout local0
    log stdout local1 notice
    chroot /var/lib/haproxy
    stats socket /var/run/haproxy.sock mode 660 level admin
    stats timeout 30s
    tune.maxconn 32768
    tune.bufsize 16384
    tune.ssl.default-dh-param 2048
    daemon

defaults
    log     global
    mode    tcp
    option  tcplog
    option  dontlognull
    option  redispatch
    option  tcp-smart-connect
    option  tcp-check
    retries 3
    timeout connect 5000
    timeout client  60000
    timeout server  60000
    timeout tunnel  1000000

# Stats page - always present so HAProxy has at least one frontend
frontend stats_frontend
    mode http
    bind 127.0.0.1:9999
    stats enable
    stats uri /stats
    stats refresh 10s

"""
        
        # Get all configured tunnels
        config_manager = ConfigManager()
        tunnels = config_manager.get_all_tunnels()
        
        if not tunnels:
            return config  # Return just global/defaults if no tunnels
        
        # For each tunnel and port create dedicated frontend+backend
        for tunnel in tunnels:
            remote_ip = getattr(tunnel, 'remote_forward_ip', None)
            tunnel_name = tunnel.name
            if not remote_ip:
                logger.debug(f"Skipping tunnel {tunnel_name}: no remote_forward_ip")
                continue
            if not getattr(tunnel, 'forwarded_ports', None):
                logger.debug(f"Skipping tunnel {tunnel_name}: no forwarded_ports")
                continue

            for port in tunnel.forwarded_ports:
                backend_name = f"{tunnel_name}_backend_{port}"
                frontend_name = f"{tunnel_name}_port_{port}"

                config += f"""backend {backend_name}
    balance roundrobin
    mode tcp
    option tcp-check
    server remote_host {remote_ip}:{port} check inter 5s fall 2 rise 1 send-proxy
    default-server inter 5s fall 2 rise 1

frontend {frontend_name}
    mode tcp
    bind 0.0.0.0:{port} 
    default_backend {backend_name}
    option socket-stats
"""
        return config
    
    def _write_config_file(self, config_content: str) -> bool:
        """Write HAProxy configuration to file."""
        try:
            HAPROXY_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            
            # Backup existing config if it exists and no backup yet
            if HAPROXY_CONFIG_FILE.exists() and not HAPROXY_BACKUP_FILE.exists():
                import shutil
                shutil.copy2(HAPROXY_CONFIG_FILE, HAPROXY_BACKUP_FILE)
                logger.info(f"Backed up original HAProxy config to {HAPROXY_BACKUP_FILE}")
            
            # Write with temp file for atomicity
            temp_file = HAPROXY_CONFIG_FILE.with_suffix('.cfg.tmp')
            with open(temp_file, 'w') as f:
                f.write(config_content)
            
            # Validate configuration
            result = subprocess.run(
                ["haproxy", "-c", "-f", str(temp_file)],
                capture_output=True,
                timeout=10,
                text=True
            )
            
            if result.returncode != 0:
                logger.error(f"HAProxy config validation failed:\n{result.stderr}")
                temp_file.unlink()
                return False
            
            # Move temp file to actual location
            temp_file.replace(HAPROXY_CONFIG_FILE)
            logger.info(f"Generated HAProxy config: {HAPROXY_CONFIG_FILE}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to write HAProxy config: {e}")
            return False
    
    def _reload_haproxy(self) -> bool:
        """Reload HAProxy configuration gracefully."""
        try:
            # Try systemctl reload first
            result = subprocess.run(
                ["systemctl", "reload", "haproxy"],
                capture_output=True,
                timeout=10
            )
            
            if result.returncode == 0:
                logger.info("HAProxy reloaded successfully")
                return True
            
            # If reload fails, try restart
            logger.debug(f"Reload failed, trying restart: {result.stderr.decode()}")
            result = subprocess.run(
                ["systemctl", "restart", "haproxy"],
                capture_output=True,
                timeout=15
            )
            
            if result.returncode == 0:
                logger.info("HAProxy restarted successfully")
                return True
            
            logger.error(f"HAProxy restart failed: {result.stderr.decode()}")
            return False
            
        except subprocess.TimeoutExpired:
            logger.error("HAProxy reload timeout")
            return False
        except Exception as e:
            logger.error(f"Failed to reload HAProxy: {e}")
            return False
    
    def create_forward(self, port: int) -> Tuple[bool, str]:
        """Add a port forward."""
        if port in self.config.forwarded_ports:
            return False, f"Port {port} already in forwarded list"
        
        # Check if port is already in use
        if self._is_port_listening(port):
            process_info = self._get_port_process(port)
            if process_info:
                return False, f"Port {port} is already in use by: {process_info}"
            else:
                return False, f"Port {port} is already in use by another process"
        
        # Add to config
        self.config.add_port(port)
        
        # Generate the new configuration for all tunnels
        config = self._generate_haproxy_config()
        
        # Write the new configuration to the file
        if not self._write_config_file(config):
            return False, "Failed to write HAProxy configuration"
        
        # Reload HAProxy to apply the new configuration
        if not self._reload_haproxy():
            return False, "Failed to reload HAProxy"
        
        return True, f"Port forward for port {port} created successfully."
    
    def remove_forward(self, ports: List[int]) -> Tuple[bool, str]:
        """Remove port forwards by updating HAProxy configuration."""
        # Get current configuration (automatically generates for all tunnels)
        current_config = self._generate_haproxy_config()
        
        # Write updated configuration
        if not self._write_config_file(current_config):
            return False, "Failed to update HAProxy configuration"
        
        # Reload HAProxy to apply changes
        if not self._reload_haproxy():
            return False, "Failed to reload HAProxy"
        
        return True, f"Port forward for {port} removed"

    def validate_and_reload(self) -> Tuple[bool, str]:
        """Validate generated HAProxy config and reload HAProxy gracefully.

        Returns (success, message).
        """
        try:
            config = self._generate_haproxy_config()
            if not config:
                return False, "No HAProxy configuration generated (no tunnels or missing data)"

            # Write and validate config
            if not self._write_config_file(config):
                return False, "HAProxy configuration validation failed"

            # Reload HAProxy
            if not self._reload_haproxy():
                return False, "HAProxy reload failed"

            return True, "HAProxy configuration validated and reloaded successfully"
        except Exception as e:
            return False, f"Error during validate_and_reload: {e}"
    
    def add_multiple_forwards(self, ports_str: str) -> Tuple[bool, str]:
        """Add multiple port forwards from comma-separated string."""
        results = []
        active_ports = []
        inactive_ports = []
        ports = [p.strip() for p in ports_str.split(',') if p.strip()]
        
        for port_str in ports:
            try:
                port = int(port_str)
                success, msg = self.create_forward(port)
                
                if success:
                    active_ports.append(port)
                    results.append(f"✓ Port {port}: ACTIVE - {msg}")
                else:
                    inactive_ports.append(port)
                    results.append(f"✗ Port {port}: INACTIVE - {msg}")
            except ValueError:
                inactive_ports.append(port_str)
                results.append(f"✗ Port '{port_str}': INACTIVE - Invalid port number")
        
        # Summary at the end
        if active_ports and inactive_ports:
            summary = f"\n\nSummary: {len(active_ports)} port(s) activated, {len(inactive_ports)} port(s) inactive due to conflicts"
            results.append(summary)
        elif active_ports:
            results.append(f"\n\nAll {len(active_ports)} port(s) activated successfully")
        elif inactive_ports:
            results.append(f"\n\nAll {len(inactive_ports)} port(s) inactive - unable to activate due to conflicts")
        
        return True, "\n".join(results)
    
    def remove_multiple_forwards(self, ports_str: str) -> Tuple[bool, str]:
        """Remove multiple port forwards from comma-separated string."""
        results = []
        ports = [p.strip() for p in ports_str.split(',') if p.strip()]
        
        for port_str in ports:
            try:
                port = int(port_str)
                success, msg = self.remove_forward(port)
                results.append(f"Port {port}: {msg}")
            except ValueError:
                results.append(f"Port '{port_str}': Invalid port number")
        
        return True, "\n".join(results)
    
    def list_forwards(self) -> List[Dict]:
        """List all configured port forwards from all tunnels."""
        forwards = []
        
        # Get all tunnels to show all forwards
        cm = ConfigManager()
        tunnels = cm.get_all_tunnels()
        
        for tunnel in tunnels:
            remote_ip = getattr(tunnel, 'remote_forward_ip', None)
            if not remote_ip:
                continue
                
            for port in tunnel.forwarded_ports:
                forwards.append({
                    "port": port,
                    "tunnel": tunnel.name,
                    "remote": f"{remote_ip}:{port}",
                    "active": self._is_port_listening(port),
                    "active_sessions": 0,
                    "stats": {
                        "connections": 0,
                        "total_bytes_sent": 0,
                        "total_bytes_received": 0,
                        "errors": 0,
                    }
                })
        
        return forwards
    
    def _is_port_listening(self, port: int) -> bool:
        """Check if a port is listening."""
        try:
            # Check using ss with multiple patterns
            result = subprocess.run(
                f"ss -tlnp 2>/dev/null | grep -E ':{port}\\b'",
                shell=True,
                capture_output=True,
                timeout=5
            )
            if result.returncode == 0:
                return True
            
            # Fallback: check with netstat
            result = subprocess.run(
                f"netstat -tlnp 2>/dev/null | grep -E ':{port}\\b'",
                shell=True,
                capture_output=True,
                timeout=5
            )
            return result.returncode == 0
        except Exception:
            return False
    
    def _get_port_process(self, port: int) -> Optional[str]:
        """Get the process using a specific port."""
        try:
            # Try ss first (more modern)
            result = subprocess.run(
                f"ss -tlnp 2>/dev/null | grep -E ':{port}\\b'",
                shell=True,
                capture_output=True,
                timeout=5,
                text=True
            )
            if result.returncode == 0 and result.stdout:
                # Parse ss output to extract process info
                # Format: ... users:(("process",pid=123,fd=4))
                import re
                match = re.search(r'users:\(\("([^"]+)",pid=(\d+)', result.stdout)
                if match:
                    process_name = match.group(1)
                    pid = match.group(2)
                    return f"{process_name} (PID: {pid})"
                return "Unknown process"
            
            # Fallback: try lsof
            result = subprocess.run(
                f"lsof -i :{port} -t 2>/dev/null | head -1",
                shell=True,
                capture_output=True,
                timeout=5,
                text=True
            )
            if result.returncode == 0 and result.stdout.strip():
                pid = result.stdout.strip()
                # Get process name from pid
                ps_result = subprocess.run(
                    f"ps -p {pid} -o comm=",
                    shell=True,
                    capture_output=True,
                    timeout=5,
                    text=True
                )
                if ps_result.returncode == 0 and ps_result.stdout.strip():
                    process_name = ps_result.stdout.strip()
                    return f"{process_name} (PID: {pid})"
                return f"PID: {pid}"
            
            return None
        except Exception:
            return None
    
    async def start_all_forwards(self) -> Tuple[bool, str]:
        """Start all configured port forwards from all tunnels."""
        # Get all tunnels from disk regardless of how manager was initialized
        cm = ConfigManager()
        tunnels = cm.get_all_tunnels()
        
        # Check if any tunnels have forwarded ports
        has_forwards = any(t.forwarded_ports for t in tunnels)
        if not has_forwards:
            return True, "No port forwards configured across all tunnels"
        
        # Generate and write configuration
        config = self._generate_haproxy_config()
        if not self._write_config_file(config):
            return False, "Failed to write HAProxy configuration"
        
        # Use systemctl to manage HAProxy consistently
        try:
            # Check if HAProxy service is already active
            check_result = subprocess.run(
                ["systemctl", "is-active", "haproxy"],
                capture_output=True,
                timeout=5,
                text=True
            )
            is_active = check_result.returncode == 0
            
            if is_active:
                # HAProxy is running - reload or restart
                logger.info("HAProxy already running, reloading configuration")
                if not self._reload_haproxy():
                    # Reload failed, try restart
                    result = subprocess.run(
                        ["systemctl", "restart", "haproxy"],
                        capture_output=True,
                        timeout=15,
                        text=True
                    )
                    if result.returncode != 0:
                        return False, f"Failed to restart HAProxy: {result.stderr}"
                    logger.info("HAProxy restarted successfully")
            else:
                # HAProxy is not running - start it
                logger.info("Starting HAProxy service via systemctl")
                result = subprocess.run(
                    ["systemctl", "start", "haproxy"],
                    capture_output=True,
                    timeout=15,
                    text=True
                )
                
                if result.returncode != 0:
                    stderr_msg = result.stderr.strip() if result.stderr else "Unknown error"
                    return False, f"Failed to start HAProxy: {stderr_msg}"
                
                logger.info("HAProxy started successfully")
            
            self.running = True
            
            # Collect all forwarded ports from all tunnels
            all_ports = set()
            for tunnel in tunnels:
                all_ports.update(tunnel.forwarded_ports)
            ports_str = ", ".join(sorted(str(p) for p in all_ports))
            msg = f"HAProxy port forwarding started for ports: {ports_str}" if ports_str else "HAProxy started (no active forwards)"
            logger.info(msg)
            return True, msg
            
        except subprocess.TimeoutExpired:
            return False, "Timeout starting/reloading HAProxy"
        except Exception as e:
            logger.error(f"Exception in start_all_forwards: {e}")
            return False, f"Error starting HAProxy: {e}"
    
    async def stop_all_forwards(self) -> Tuple[bool, str]:
        """Stop all configured port forwards."""
        try:
            result = subprocess.run(
                ["systemctl", "stop", "haproxy"],
                capture_output=True,
                timeout=10
            )
            
            self.running = False
            return True, "HAProxy port forwarding stopped"
            
        except Exception as e:
            return False, f"Error stopping HAProxy: {e}"
    
    async def restart_all_forwards(self) -> Tuple[bool, str]:
        """Restart all configured port forwards."""
        await self.stop_all_forwards()
        await asyncio.sleep(1)
        return await self.start_all_forwards()
    
    def start_in_background(self) -> bool:
        """Start forwarding in background (HAProxy handles this internally)."""
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            async def run():
                success, msg = await self.start_all_forwards()
                if not success:
                    logger.error(msg)
                    return False
                
                # Keep running
                while self.running:
                    await asyncio.sleep(1)
                
                return True
            
            loop.run_until_complete(run())
            return True
        except Exception as e:
            logger.error(f"Failed to start background forwarding: {e}")
            return False


# Backward compatibility alias
ForwardManager = HAProxyManager
