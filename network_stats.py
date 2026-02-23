import ctypes
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import os
from scipy import sparse
from scipy.sparse.csgraph import connected_components
from scipy.sparse.linalg import eigs

# -------------------------------------------------------------------
# FFI Binding to temporal_contact_matrix.{dll/so/dylib}
# -------------------------------------------------------------------

LIB_PATH = os.path.join(os.path.dirname(__file__), "temporal_contact_matrix\temporal_contact_matrix.dll")
lib = ctypes.CDLL(LIB_PATH)

lib.set_exit_node_label.argtypes = [ctypes.c_char_p]
lib.set_exit_node_label.restype = ctypes.c_int

lib.init_temporal_db.argtypes = [ctypes.c_char_p]
lib.init_temporal_db.restype = ctypes.c_int

lib.get_total_node_count.argtypes = []
lib.get_total_node_count.restype = ctypes.c_int

lib.get_adjacency_matrix_for_window.argtypes = [
    ctypes.c_int64,
    ctypes.c_int64,
    ctypes.POINTER(ctypes.c_float),
]
lib.get_adjacency_matrix_for_window.restype = ctypes.c_int

lib.shutdown_temporal_db.argtypes = []
lib.shutdown_temporal_db.restype = None

class EdgeTuple(ctypes.Structure):
    _fields_ = [("src", ctypes.c_int), ("tgt", ctypes.c_int), ("weight", ctypes.c_float)]

lib.get_edge_list_for_window.argtypes = [
    ctypes.c_int64, ctypes.c_int64,
    ctypes.POINTER(EdgeTuple), ctypes.c_int
]
lib.get_edge_list_for_window.restype = ctypes.c_int

# -------------------------------------------------------------------
# Adjacency Matrix Wrapper
# -------------------------------------------------------------------

class AdjacencyMatrixGenerator:
    def __init__(self, filename: str, exit_token: str = "EXIT"):
        # configure exit token
        rc = lib.set_exit_node_label(exit_token.encode("utf-8"))
        if rc != 1:
            raise RuntimeError(f"Invalid exit token: {exit_token!r}")

        if not lib.init_temporal_db(filename.encode("utf-8")):
            raise RuntimeError("Failed to initialize temporal DB.")
        self.num_nodes = lib.get_total_node_count()
        print(f"[DEBUG] Temporal DB loaded with {self.num_nodes} nodes.")

    def query_sparse(self, start_ts: int, end_ts: int, directed: bool = False):
        N = self.num_nodes
        count = lib.get_edge_list_for_window(start_ts, end_ts, None, 0)
        if count < 0:
            raise RuntimeError("Failed to count edges")
        if count == 0:
            return sparse.csr_matrix((N, N), dtype=np.float32), np.zeros(N, dtype=bool)

        EdgeArrayType = EdgeTuple * count
        edge_array = EdgeArrayType()
        filled = lib.get_edge_list_for_window(start_ts, end_ts, edge_array, count)
        if filled != count:
            raise RuntimeError("Mismatch in edge count")

        rows = np.empty(count, dtype=np.int32)
        cols = np.empty(count, dtype=np.int32)
        weights = np.empty(count, dtype=np.float32)
        for i in range(count):
            rows[i], cols[i], weights[i] = edge_array[i].src, edge_array[i].tgt, edge_array[i].weight

        mat = sparse.coo_matrix((weights, (rows, cols)), shape=(N, N)).tocsr()
        in_hospital = mat.diagonal() > 0
        return (mat if directed else mat.maximum(mat.T)), in_hospital

    def shutdown(self):
        lib.shutdown_temporal_db()


# -------------------------------------------------------------------
# Sweep Logic
# -------------------------------------------------------------------

def temporal_sweep(gen, t_start, step, num_steps, window_size, directed=False):
    records = []

    for i in range(num_steps):
        print(f'Calculating stats... ({i}/{num_steps})')
        t = t_start + i * step
        window_end = t + window_size

        try:
            adj, hospital_mask = gen.query_sparse(t, window_end, directed=directed)
        except Exception as e:
            print(f"[WARN] Query failed at t={t}: {e}")
            continue

        present = np.where(hospital_mask)[0]
        if len(present) == 0:
            print(f"[INFO] No nodes present in window starting {t}")
            continue

        adj_present = adj[present][:, present]
        adj_present.setdiag(0)
        adj_present.eliminate_zeros()

        N = adj_present.shape[0]
        total_weight = adj_present.sum()
        strength = np.array(adj_present.sum(axis=1)).flatten()

        adj_binary = adj_present.copy()
        adj_binary.data[:] = 1
        degree = np.array(adj_binary.sum(axis=1)).flatten()

        n_comp, labels = connected_components(adj_binary, directed=False)
        largest_comp_size = np.bincount(labels).max()

        try:
            vals, vecs = eigs(adj_present.astype(float), k=1, which='LR')
            eig = np.abs(vecs[:, 0].real)
            eig /= eig.sum()
            eig_mean, eig_max = eig.mean(), eig.max()
        except Exception:
            eig_mean = eig_max = float('nan')

        records.append({
            'time': t,
            'num_nodes': N,
            'total_weight': total_weight,
            'num_edges': adj_present.nnz,
            'strength_mean': strength.mean(),
            'strength_max': strength.max(),
            'strength_min': strength.min(),
            'degree_mean': degree.mean(),
            'degree_max': degree.max(),
            # 'degree_min': degree.min(), # boring because its always 1
            'mean_edge_weight': (total_weight / adj_present.nnz) if adj_present.nnz > 0 else np.nan,
            'weighted_density': total_weight / (N * (N - 1)) if N > 1 else 0,
            'unweighted_density': adj_present.nnz / (N * (N - 1)) if N > 1 else 0,
            'connected_components': n_comp,
            'largest_component': largest_comp_size,
            'eig_centrality_mean': eig_mean,
            'eig_centrality_max': eig_max,
        })

    return pd.DataFrame(records)


def plot_temporal_stats(df):
    metrics = [col for col in df.columns if col != "time"]
    fig, axes = plt.subplots(len(metrics), 1, figsize=(10, 3 * len(metrics)), sharex=True)
    axes = [axes] if len(metrics) == 1 else axes

    for ax, metric in zip(axes, metrics):
        ax.plot(df["time"], df[metric], label=metric)
        ax.set_ylabel(metric)
        ax.legend()

    axes[-1].set_xlabel("Time (epoch)")
    plt.tight_layout()
    plt.show()

# -------------------------------------------------------------------
# Main
# -------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Sweep temporal contact network.")
    parser.add_argument("graph_file", help="Path to .txt/.bin graph file")
    parser.add_argument("--start", type=int, required=True, help="Start time (epoch)")
    parser.add_argument("--step", type=int, default=3600, help="Step size (seconds)")
    parser.add_argument("--steps", type=int, required=True, help="Number of steps to sweep")
    parser.add_argument("--window", type=int, default=3600, help="Window size (seconds)")
    parser.add_argument("--directed", action="store_true", help="Keep directed edges")
    parser.add_argument("--exit-token", default="EXIT", help="Node label treated as EXIT (filtered)")

    args = parser.parse_args()

    gen = AdjacencyMatrixGenerator(args.graph_file, exit_token=args.exit_token)

    print("[INFO] Beginning temporal sweep...")
    df = temporal_sweep(
        gen,
        t_start=args.start,
        step=args.step,
        num_steps=args.steps,
        window_size=args.window,
        directed=args.directed
    )

    gen.shutdown()

    print("[INFO] Sweep complete. Plotting results...")
    plot_temporal_stats(df)

if __name__ == "__main__":
    main()
