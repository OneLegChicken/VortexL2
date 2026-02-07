#!/usr/bin/env python3
"""
VortexL2 Connection Pooling

Reuses connections and implements chaotic connection patterns
to defeat flow-based DPI detection and reduce server fingerprinting.
"""

import threading
import time
import random
import logging
from typing import Dict, List, Tuple
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta


logger = logging.getLogger(__name__)


@dataclass
class ConnectionMetrics:
    """Metrics for a connection pool."""
    created_at: datetime = field(default_factory=datetime.now)
    last_used: datetime = field(default_factory=datetime.now)
    packets_sent: int = 0
    packets_received: int = 0
    bytes_sent: int = 0
    bytes_received: int = 0
    reuses: int = 0  # How many times connection was reused
    idle_time: float = 0  # Total idle time in seconds


class ConnectionPool:
    """
    Manages connection pooling and chaotic connection patterns
    to obfuscate traffic from DPI.
    """
    
    def __init__(self, pool_size: int = 8, reuse_probability: float = 0.7):
        """
        Initialize connection pool.
        
        Args:
            pool_size: Number of persistent connections to maintain
            reuse_probability: Probability of reusing existing connection (0-1)
        """
        self.pool_size = pool_size
        self.reuse_probability = reuse_probability
        self.connections: Dict[int, Dict] = {}
        self.metrics: Dict[int, ConnectionMetrics] = {}
        self.connection_id_counter = 0
        self.lock = threading.RLock()
        self.stats = {
            "total_created": 0,
            "total_reused": 0,
            "total_closed": 0,
            "current_active": 0,
        }
    
    def create_connection(self) -> int:
        """
        Create a new connection and return its ID.
        
        Returns:
            Connection ID (integer)
        """
        with self.lock:
            conn_id = self.connection_id_counter
            self.connection_id_counter += 1
            
            self.connections[conn_id] = {
                "state": "active",
                "created_at": time.time(),
                "last_activity": time.time(),
            }
            
            self.metrics[conn_id] = ConnectionMetrics()
            
            self.stats["total_created"] += 1
            self.stats["current_active"] = len([
                c for c in self.connections.values() if c["state"] == "active"
            ])
            
            logger.debug(f"Created connection {conn_id}")
            return conn_id
    
    def close_connection(self, conn_id: int) -> bool:
        """
        Close a connection.
        
        Returns:
            True if closed, False if not found
        """
        with self.lock:
            if conn_id in self.connections:
                self.connections[conn_id]["state"] = "closed"
                self.stats["total_closed"] += 1
                logger.debug(f"Closed connection {conn_id}")
                return True
            return False
    
    def get_connection(self, force_new: bool = False) -> int:
        """
        Get a connection (reused or new).
        Implements chaotic selection to defeat flow-based detection.
        
        Args:
            force_new: Force creation of new connection
        
        Returns:
            Connection ID
        """
        with self.lock:
            active_connections = [
                c_id for c_id, conn in self.connections.items()
                if conn["state"] == "active"
            ]
            
            # Chaotic logic: randomly decide to use existing or create new
            if not force_new and active_connections and random.random() < self.reuse_probability:
                # Reuse existing connection (but randomize selection)
                conn_id = random.choice(active_connections)
                self.connections[conn_id]["last_activity"] = time.time()
                self.metrics[conn_id].reuses += 1
                self.stats["total_reused"] += 1
                logger.debug(f"Reusing connection {conn_id} (reuses: {self.metrics[conn_id].reuses})")
                return conn_id
            
            # Check pool size - create new if below limit
            if len(active_connections) < self.pool_size:
                return self.create_connection()
            
            # Pool full - still try to reuse or close old one and create new
            if random.random() < 0.5 and active_connections:
                # Occasionally force reuse even when pool full
                return random.choice(active_connections)
            else:
                # Close old connection and create new (connection cycling)
                old_conn_id = self._get_oldest_connection()
                if old_conn_id is not None:
                    self.close_connection(old_conn_id)
                
                return self.create_connection()
    
    def update_metrics(self, conn_id: int, bytes_sent: int = 0, bytes_received: int = 0) -> None:
        """
        Update connection metrics.
        """
        with self.lock:
            if conn_id in self.metrics:
                metrics = self.metrics[conn_id]
                if bytes_sent > 0:
                    metrics.packets_sent += 1
                    metrics.bytes_sent += bytes_sent
                if bytes_received > 0:
                    metrics.packets_received += 1
                    metrics.bytes_received += bytes_received
                metrics.last_used = datetime.now()
    
    def _get_oldest_connection(self) -> int:
        """Get the oldest active connection (LRU)."""
        with self.lock:
            oldest_conn_id = None
            oldest_time = time.time()
            
            for conn_id, conn in self.connections.items():
                if conn["state"] == "active":
                    activity_time = conn["last_activity"]
                    if activity_time < oldest_time:
                        oldest_time = activity_time
                        oldest_conn_id = conn_id
            
            return oldest_conn_id
    
    def get_chaotic_connection_pattern(self, num_requests: int = 10) -> List[Tuple[int, int]]:
        """
        Generate chaotic connection pattern for fooling DPI.
        
        Returns a list of (connection_id, delay_ms) tuples.
        
        Args:
            num_requests: Number of requests to generate pattern for
        """
        pattern = []
        for i in range(num_requests):
            # Mix old connections with new ones
            if random.random() < 0.3:  # 30% new connections
                conn_id = self.create_connection()
            else:  # 70% reuse
                conn_id = self.get_connection()
            
            # Random delay between requests (5-500ms)
            delay = random.randint(5, 500)
            
            # Occasionally add longer delays to break patterns
            if random.random() < 0.1:
                delay = random.randint(1000, 5000)
            
            pattern.append((conn_id, delay))
        
        return pattern
    
    def get_pool_status(self) -> Dict:
        """Get current pool status."""
        with self.lock:
            active_conns = [c for c in self.connections.values() if c["state"] == "active"]
            closed_conns = [c for c in self.connections.values() if c["state"] == "closed"]
            
            total_bytes_sent = sum(m.bytes_sent for m in self.metrics.values())
            total_bytes_received = sum(m.bytes_received for m in self.metrics.values())
            total_packets = sum(m.packets_sent + m.packets_received for m in self.metrics.values())
            
            return {
                "pool_size": self.pool_size,
                "active_connections": len(active_conns),
                "closed_connections": len(closed_conns),
                "total_bytes_sent": total_bytes_sent,
                "total_bytes_received": total_bytes_received,
                "total_packets": total_packets,
                "reuse_rate": (self.stats["total_reused"] / max(1, self.stats["total_created"] + self.stats["total_reused"])) * 100,
                "stats": self.stats,
            }
    
    def print_status_report(self) -> str:
        """Generate status report."""
        status = self.get_pool_status()
        
        report = """
=== Connection Pool Status ===

CONFIGURATION:
  Pool size: {pool_size}
  Active connections: {active_connections}/{pool_size}
  Closed connections: {closed_connections}

STATISTICS:
  Total created: {total_created}
  Total reused: {total_reused}
  Total closed: {total_closed}
  Reuse rate: {reuse_rate:.1f}%

TRAFFIC:
  Total bytes sent: {total_bytes_sent:,}
  Total bytes received: {total_bytes_received:,}
  Total packets: {total_packets}

OBFUSCATION:
  Using chaotic connection patterns
  Mixing connection reuse and creation
  Random delays between requests
  Variable packet sizes per connection
""".format(**status, **status["stats"])
        
        return report


class ConnectionPoolManager:
    """
    Global manager for connection pooling across all tunnels.
    """
    
    def __init__(self):
        self.tunnel_pools: Dict[str, ConnectionPool] = {}
        self.lock = threading.RLock()
    
    def get_pool(self, tunnel_name: str, pool_size: int = 8) -> ConnectionPool:
        """
        Get or create connection pool for a tunnel.
        
        Args:
            tunnel_name: Name of the tunnel
            pool_size: Size of pool if creating new
        
        Returns:
            ConnectionPool instance
        """
        with self.lock:
            if tunnel_name not in self.tunnel_pools:
                self.tunnel_pools[tunnel_name] = ConnectionPool(pool_size=pool_size)
                logger.info(f"Created connection pool for tunnel: {tunnel_name} (size: {pool_size})")
            
            return self.tunnel_pools[tunnel_name]
    
    def get_all_status(self) -> Dict[str, Dict]:
        """Get status of all tunnel pools."""
        with self.lock:
            return {
                tunnel_name: pool.get_pool_status()
                for tunnel_name, pool in self.tunnel_pools.items()
            }


# Global connection pool manager
_pool_manager = None


def get_pool_manager() -> ConnectionPoolManager:
    """Get or create global connection pool manager."""
    global _pool_manager
    if _pool_manager is None:
        _pool_manager = ConnectionPoolManager()
    return _pool_manager


def setup_connection_pooling(tunnel_name: str, pool_size: int = 8) -> Tuple[bool, str]:
    """
    Setup connection pooling for a tunnel.
    
    Returns:
        (success, message)
    """
    manager = get_pool_manager()
    pool = manager.get_pool(tunnel_name, pool_size)
    
    return True, f"Connection pool initialized: {tunnel_name} (size: {pool_size})"
