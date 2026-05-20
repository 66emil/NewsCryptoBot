"""
LSTM model for time-series score prediction (TSM).
Architecture: LSTM(128) → dropout → LSTM(64) → dropout → Linear(1) → Sigmoid
Input:  (batch, seq_len=60, features=10)
Output: (batch, 1) ∈ [0, 1]
"""
import torch
import torch.nn as nn


class LSTMPriceModel(nn.Module):

    def __init__(
        self,
        input_size: int = 10,
        hidden1: int = 128,
        hidden2: int = 64,
        dropout: float = 0.2,
    ) -> None:
        super().__init__()
        self.lstm1 = nn.LSTM(input_size, hidden1, batch_first=True)
        self.drop1 = nn.Dropout(dropout)
        self.lstm2 = nn.LSTM(hidden1, hidden2, batch_first=True)
        self.drop2 = nn.Dropout(dropout)
        self.fc = nn.Linear(hidden2, 1)
        self.sigmoid = nn.Sigmoid()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out, _ = self.lstm1(x)
        out = self.drop1(out)
        out, _ = self.lstm2(out)
        out = self.drop2(out)
        out = self.fc(out[:, -1, :])
        return self.sigmoid(out)
