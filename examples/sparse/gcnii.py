"""
[Simple and Deep Graph Convolutional Networks]
(https://arxiv.org/abs/2007.02133)
"""

import math

import torch
import torch.nn as nn
import torch.nn.functional as F
from dgl.data import CoraGraphDataset
from dgl.mock_sparse import create_from_coo, diag, identity
from torch.optim import Adam


class GCNIIConvolution(nn.Module):
    def __init__(self, in_size, out_size):
        super().__init__()
        self.out_size = out_size
        self.weight = nn.Linear(in_size, out_size, bias=False)

    ############################################################################
    # (HIGHLIGHT) Take the advantage of DGL sparse APIs to implement the GCNII
    # forward process.
    ############################################################################
    def forward(self, A_norm, H, H0, lamda, alpha, l):
        beta = math.log(lamda / l + 1)

        # Multiply a sparse matrix by a dense matrix.
        H = A_norm @ H
        support = (1 - alpha) * H + alpha * H0
        H = (1 - beta) * support + beta * self.weight(support)
        return H


class GCNII(nn.Module):
    def __init__(
        self,
        in_size,
        out_size,
        hidden_size,
        n_layers,
        lamda,
        alpha,
        dropout=0.5,
    ):
        super().__init__()
        self.hidden_size = hidden_size
        self.n_layers = n_layers
        self.lamda = lamda
        self.alpha = alpha

        self.FC_layers = nn.ModuleList()
        self.FC_layers.append(nn.Linear(in_size, hidden_size))
        self.FC_layers.append(nn.Linear(hidden_size, out_size))

        self.CONV_layers = nn.ModuleList()
        for _ in range(n_layers):
            self.CONV_layers.append(GCNIIConvolution(hidden_size, hidden_size))
        self.activation = nn.ReLU()
        self.dropout = dropout

    def forward(self, A_norm, feature):
        H = feature
        H = F.dropout(H, self.dropout, training=self.training)
        H = self.FC_layers[0](H)
        H = self.activation(H)
        H0 = H
        for i, conv in enumerate(self.CONV_layers):
            H = F.dropout(H, self.dropout, training=self.training)
            H = conv(A_norm, H, H0, self.lamda, self.alpha, i + 1)
            H = self.activation(H)
        H = F.dropout(H, self.dropout, training=self.training)
        H = self.FC_layers[-1](H)

        return H


def evaluate(model, A_norm, H, label, val_mask, test_mask):
    model.eval()
    logits = model(A_norm, H)
    pred = logits.argmax(dim=1)

    # Compute accuracy on validation/test set.
    val_acc = (pred[val_mask] == label[val_mask]).float().mean()
    test_acc = (pred[test_mask] == label[test_mask]).float().mean()
    return val_acc, test_acc


def train(model, g, A_norm, H):
    label = g.ndata["label"]
    train_mask = g.ndata["train_mask"]
    val_mask = g.ndata["val_mask"]
    test_mask = g.ndata["test_mask"]
    optimizer = Adam(model.parameters(), lr=0.01, weight_decay=5e-4)

    loss_fcn = nn.CrossEntropyLoss()

    for epoch in range(1500):
        model.train()
        optimizer.zero_grad()

        # Forward.
        logits = model(A_norm, H)

        # Compute loss with nodes in the training set.
        loss = loss_fcn(logits[train_mask], label[train_mask])

        # Backward.
        loss.backward()
        optimizer.step()

        # Evaluate the prediction.
        val_acc, test_acc = evaluate(
            model, A_norm, H, label, val_mask, test_mask
        )
        if epoch % 20 == 0:
            print(
                f"In epoch {epoch}, loss: {loss:.3f}, val acc: {val_acc:.3f}"
                f", test acc: {test_acc:.3f}"
            )


if __name__ == "__main__":
    # Training settings.
    hidden_size = 64
    n_layers = 64
    dropout = 0.5

    # Hyperparameter settings.
    alpha = 0.2
    lamda = 0.5

    # If CUDA is available, use GPU to accelerate the training, use CPU
    # otherwise.
    dev = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

    # Load graph from the existing dataset.
    dataset = CoraGraphDataset()
    g = dataset[0].to(dev)
    num_classes = dataset.num_classes
    H = g.ndata["feat"]

    # Create the adjacency matrix of graph.
    src, dst = g.edges()
    N = g.num_nodes()
    A = create_from_coo(dst, src, shape=(N, N))

    ############################################################################
    # (HIGHLIGHT) Compute the symmetrically normalized adjacency matrix with
    # Sparse Matrix API
    ############################################################################
    I = identity(A.shape, device=dev)
    A_hat = A + I
    D_hat = diag(A_hat.sum(1)) ** -0.5
    A_norm = D_hat @ A_hat @ D_hat

    # Create model.
    in_size = H.shape[1]
    out_size = num_classes
    model = GCNII(
        in_size,
        out_size,
        hidden_size,
        n_layers,
        lamda,
        alpha,
        dropout,
    ).to(dev)

    # Kick off training.
    train(model, g, A_norm, H)
