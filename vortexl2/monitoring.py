#!/usr/bin/env python3
"""
VortexL2 Monitoring & Alerting System

Real-time monitoring of tunnel health, performance metrics,
and automated alerting for anomalies and failures.
"""

import logging
import json
import time
import subprocess
import socket
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path


logger = logging.getLogger(__name__)


@dataclass
class PerformanceMetrics:
    """Performance metrics snapshot."""
    timestamp: str
    tunnel_name: str
    throughput_mbps: float
    latency_ms: float
    packet_loss: float
    connection_status: str
    active_ports: int
    errors: int


@dataclass
class AlertEvent:
    """Alert event."""
    timestamp: str
    severity: str  # CRITICAL, WARNING, INFO
    source: str
    message: str
    metrics: Optional[Dict] = None


class MetricsCollector:
    """Collects performance metrics from tunnels."""
    
    @staticmethod
    def run_command(cmd: str) -> Tuple[bool, str]:
        """Execute shell command."""
        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=10
            )
            return result.returncode == 0, result.stdout if result.returncode == 0 else result.stderr
        except Exception as e:
            return False, str(e)
    
    @staticmethod
    def get_interface_stats(interface_name: str) -> Dict:
        """Get interface statistics from `/sys/class/net/`."""
        try:
            stats_path = Path(f"/sys/class/net/{interface_name}/statistics")
            
            if not stats_path.exists():
                return {}
            
            return {
                "rx_bytes": int((stats_path / "rx_bytes").read_text().strip()),
                "tx_bytes": int((stats_path / "tx_bytes").read_text().strip()),
                "rx_packets": int((stats_path / "rx_packets").read_text().strip()),
                "tx_packets": int((stats_path / "tx_packets").read_text().strip()),
                "rx_errors": int((stats_path / "rx_errors").read_text().strip()),
                "tx_errors": int((stats_path / "tx_errors").read_text().strip()),
                "rx_dropped": int((stats_path / "rx_dropped").read_text().strip()),
                "tx_dropped": int((stats_path / "tx_dropped").read_text().strip()),
            }
        except Exception as e:
            logger.warning(f"Could not read interface stats for {interface_name}: {e}")
            return {}
    
    @staticmethod
    def calculate_throughput(current: Dict, previous: Dict, time_delta_sec: float) -> float:
        """Calculate throughput in Mbps."""
        if not current or not previous or time_delta_sec <= 0:
            return 0.0
        
        tx_delta = current.get("tx_bytes", 0) - previous.get("tx_bytes", 0)
        rx_delta = current.get("rx_bytes", 0) - previous.get("rx_bytes", 0)
        
        total_bytes = tx_delta + rx_delta
        megabits = (total_bytes * 8) / 1_000_000
        
        return megabits / time_delta_sec
    
    @staticmethod
    def measure_latency(remote_ip: str) -> float:
        """Measure ping latency to remote endpoint."""
        try:
            # Use ping with timeout
            _, output = MetricsCollector.run_command(
                f"ping -c 1 -W 2 {remote_ip} 2>/dev/null | grep time= | cut -d'=' -f4 | cut -d' ' -f1"
            )
            
            if output:
                return float(output.strip())
        except Exception:
            pass
        
        return -1.0
    
    @staticmethod
    def calculate_packet_loss(interface_stats: Dict) -> float:
        """Calculate packet loss percentage."""
        total_rx = interface_stats.get("rx_packets", 0) + interface_stats.get("rx_errors", 0)
        errors = interface_stats.get("rx_errors", 0)
        
        if total_rx <= 0:
            return 0.0
        
        return (errors / total_rx) * 100


class AlertThresholds:
    """Alert thresholds configuration."""
    
    # Performance thresholds
    MIN_THROUGHPUT_MBPS = 1.0           # Alert if below this
    MAX_LATENCY_MS = 200.0              # Alert if above this
    MAX_PACKET_LOSS_PCT = 5.0           # Alert if above this
    
    # Process/Service thresholds
    MAX_CONSECUTIVE_FAILURES = 3        # Trigger alert after this many failures
    MAX_UNAVAILABILITY_SEC = 300        # Alert after 5 minutes down
    
    # Port forward thresholds
    MIN_STABLE_PORTS = 0.8              # Alert if less than 80% of ports are up


class AlertManager:
    """Manages alerts and notifications."""
    
    def __init__(self, log_dir: Path = Path("/var/log/vortexl2")):
        self.log_dir = log_dir
        self.alert_log = log_dir / "alerts.log"
        self.alerts: List[AlertEvent] = []
        self.setup_logging()
    
    def setup_logging(self):
        """Setup alert logging."""
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            handler = logging.FileHandler(self.alert_log)
            formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
            handler.setFormatter(formatter)
            logger.addHandler(handler)
        except Exception as e:
            logger.warning(f"Could not setup alert logging: {e}")
    
    def create_alert(self, severity: str, source: str, message: str, metrics: Dict = None) -> AlertEvent:
        """Create and log an alert."""
        alert = AlertEvent(
            timestamp=datetime.now().isoformat(),
            severity=severity,
            source=source,
            message=message,
            metrics=metrics
        )
        
        self.alerts.append(alert)
        self._log_alert(alert)
        self._send_notification(alert)
        
        return alert
    
    def _log_alert(self, alert: AlertEvent) -> None:
        """Log alert to file."""
        try:
            level = {
                "CRITICAL": logging.CRITICAL,
                "WARNING": logging.WARNING,
                "INFO": logging.INFO,
            }.get(alert.severity, logging.INFO)
            
            logger.log(level, f"[{alert.source}] {alert.message}")
        except Exception as e:
            logger.error(f"Failed to log alert: {e}")
    
    def _send_notification(self, alert: AlertEvent) -> None:
        """Send alert notification."""
        # Can be extended to send emails, webhooks, etc.
        
        if alert.severity == "CRITICAL":
            # For critical alerts, try to send system notification
            try:
                subprocess.run(
                    ["notify-send", "-u", "critical", "VortexL2 Alert", alert.message],
                    timeout=5,
                    capture_output=True
                )
            except Exception:
                pass
    
    def get_recent_alerts(self, hours: int = 1, severity: str = None) -> List[AlertEvent]:
        """Get recent alerts."""
        cutoff = datetime.now() - timedelta(hours=hours)
        
        recent = [a for a in self.alerts if datetime.fromisoformat(a.timestamp) > cutoff]
        
        if severity:
            recent = [a for a in recent if a.severity == severity]
        
        return recent
    
    def export_alerts_json(self, filepath: Path) -> bool:
        """Export alerts to JSON file."""
        try:
            data = [asdict(a) for a in self.alerts]
            with open(filepath, 'w') as f:
                json.dump(data, f, indent=2)
            return True
        except Exception as e:
            logger.error(f"Failed to export alerts: {e}")
            return False


class TunnelMonitor:
    """Monitors tunnel health and performance."""
    
    def __init__(self, alert_manager: AlertManager = None):
        self.alert_manager = alert_manager or AlertManager()
        self.metrics_history: Dict[str, List[PerformanceMetrics]] = {}
        self.previous_stats: Dict[str, Dict] = {}
        self.failure_counts: Dict[str, int] = {}
    
    def collect_metrics(self, tunnel_name: str, interface_name: str, remote_ip: str) -> PerformanceMetrics:
        """Collect all metrics for a tunnel."""
        # Get current stats
        current_stats = MetricsCollector.get_interface_stats(interface_name)
        
        # Calculate throughput
        previous = self.previous_stats.get(tunnel_name, current_stats)
        time_delta = 30  # Assume 30 seconds between checks
        throughput = MetricsCollector.calculate_throughput(current_stats, previous, time_delta)
        
        # Measure latency
        latency = MetricsCollector.measure_latency(remote_ip)
        
        # Calculate packet loss
        packet_loss = MetricsCollector.calculate_packet_loss(current_stats)
        
        # Determine status
        status = self._get_connection_status(throughput, latency, packet_loss)
        
        # Store for next iteration
        self.previous_stats[tunnel_name] = current_stats
        
        # Create metrics object
        metrics = PerformanceMetrics(
            timestamp=datetime.now().isoformat(),
            tunnel_name=tunnel_name,
            throughput_mbps=throughput,
            latency_ms=latency,
            packet_loss=packet_loss,
            connection_status=status,
            active_ports=0,  # Would be populated from config
            errors=current_stats.get("rx_errors", 0) + current_stats.get("tx_errors", 0)
        )
        
        # Store in history
        if tunnel_name not in self.metrics_history:
            self.metrics_history[tunnel_name] = []
        self.metrics_history[tunnel_name].append(metrics)
        
        # Keep only last 1000 metrics per tunnel
        if len(self.metrics_history[tunnel_name]) > 1000:
            self.metrics_history[tunnel_name] = self.metrics_history[tunnel_name][-1000:]
        
        return metrics
    
    def _get_connection_status(self, throughput: float, latency: float, packet_loss: float) -> str:
        """Determine connection status."""
        if throughput <= 0 or latency < 0:
            return "DISCONNECTED"
        elif packet_loss > AlertThresholds.MAX_PACKET_LOSS_PCT:
            return "DEGRADED"
        elif latency > AlertThresholds.MAX_LATENCY_MS:
            return "HIGH_LATENCY"
        elif throughput < AlertThresholds.MIN_THROUGHPUT_MBPS:
            return "LOW_THROUGHPUT"
        else:
            return "HEALTHY"
    
    def check_alert_conditions(self, metrics: PerformanceMetrics) -> None:
        """Check for alert conditions."""
        tunnel_name = metrics.tunnel_name
        
        # Low throughput alert
        if metrics.connection_status == "DISCONNECTED":
            self._increment_failure_count(tunnel_name)
            if self.failure_counts[tunnel_name] >= AlertThresholds.MAX_CONSECUTIVE_FAILURES:
                self.alert_manager.create_alert(
                    "CRITICAL",
                    tunnel_name,
                    f"Tunnel disconnected for {self.failure_counts[tunnel_name]} checks",
                    asdict(metrics)
                )
        else:
            self._reset_failure_count(tunnel_name)
        
        # High latency alert
        if metrics.latency_ms > AlertThresholds.MAX_LATENCY_MS:
            self.alert_manager.create_alert(
                "WARNING",
                tunnel_name,
                f"High latency detected: {metrics.latency_ms:.1f}ms",
                asdict(metrics)
            )
        
        # Packet loss alert
        if metrics.packet_loss > AlertThresholds.MAX_PACKET_LOSS_PCT:
            self.alert_manager.create_alert(
                "WARNING",
                tunnel_name,
                f"Packet loss detected: {metrics.packet_loss:.2f}%",
                asdict(metrics)
            )
        
        # Low throughput alert
        if metrics.throughput_mbps < AlertThresholds.MIN_THROUGHPUT_MBPS and metrics.throughput_mbps > 0:
            self.alert_manager.create_alert(
                "INFO",
                tunnel_name,
                f"Low throughput: {metrics.throughput_mbps:.2f} Mbps",
                asdict(metrics)
            )
    
    def _increment_failure_count(self, tunnel_name: str) -> None:
        if tunnel_name not in self.failure_counts:
            self.failure_counts[tunnel_name] = 0
        self.failure_counts[tunnel_name] += 1
    
    def _reset_failure_count(self, tunnel_name: str) -> None:
        self.failure_counts[tunnel_name] = 0
    
    def get_tunnel_report(self, tunnel_name: str) -> str:
        """Generate report for a tunnel."""
        history = self.metrics_history.get(tunnel_name, [])
        if not history:
            return f"No metrics available for tunnel: {tunnel_name}"
        
        latest = history[-1]
        avg_throughput = sum(m.throughput_mbps for m in history[-60:]) / max(1, len(history[-60:]))
        avg_latency = sum(m.latency_ms for m in history[-60:] if m.latency_ms >= 0) / max(1, len([m for m in history[-60:] if m.latency_ms >= 0]))
        
        report = f"""
=== Tunnel Report: {tunnel_name} ===

CURRENT STATUS:
  Connection: {latest.connection_status}
  Throughput: {latest.throughput_mbps:.2f} Mbps
  Latency: {latest.latency_ms:.1f}ms
  Packet Loss: {latest.packet_loss:.2f}%
  Errors: {latest.errors}

STATISTICS (Last 60 readings):
  Average Throughput: {avg_throughput:.2f} Mbps
  Average Latency: {avg_latency:.1f}ms
  Min Throughput: {min(m.throughput_mbps for m in history[-60:]) if history[-60:] else 0:.2f} Mbps
  Max Latency: {max(m.latency_ms for m in history[-60:]) if history[-60:] else 0:.1f}ms
"""
        return report


def create_monitoring_system() -> Tuple[AlertManager, TunnelMonitor]:
    """Factory function to create monitoring system."""
    alert_mgr = AlertManager()
    monitor = TunnelMonitor(alert_mgr)
    return alert_mgr, monitor
