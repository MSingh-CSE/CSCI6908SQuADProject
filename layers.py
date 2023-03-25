"""Assortment of layers for use in models.py.

Authors:
    Sahil Khose (sahilkhose18@gmail.com)
    Abhiraj Tiwari (abhirajtiwari@gmail.com)
"""
import math
import torch
import torch.nn as nn
import torch.nn.functional as F

from util import masked_softmax

# --------------- Model Layers ------------------
class InputEmbeddingLayer(nn.Module):
    """Embedding layer used in QANet, without character-level embedding.
    Word embedding of 300-D
    """
    def __init__(self, word_vectors, drop_prob, hidden_size):
        """
        @param word_vectors (torch.Tensor): Pre-trained word vectors.
        @param drop_prob (float): Probability of zero-ing out activations.
        """
        super(InputEmbeddingLayer, self).__init__()

        self.embed = nn.Embedding.from_pretrained(word_vectors)
        self.dropout = torch.nn.Dropout(drop_prob)
        self.hwy = HighwayEncoder(2, word_vectors.size(1)) 
        
    def forward(self, x):
        """Looks up word embeddings for the words in a batch of sentences.
        @param x (torch.Tensor): (batch_size, sent_len)
        @returns emb (torch.Tensor): Word embeddings for the batch of sentences. (batch_size, word_embed, sent_len)
        """
        emb = self.embed(x)  # (batch_size, sent_len, word_embed)
        emb = self.dropout(emb)  # (batch_size, sent_len, word_embed)
        emb = self.hwy(emb)
        return emb.permute(0, 2, 1)  # (batch_size, word_embed, sent_len)


class EmbeddingEncoderLayer(nn.Module):
    """Embedding Encoder layer which encodes using convolution, self attention and feed forward network.
    Takes input from Input Embedding Layer.
    """
    def __init__(self, conv_layers, kernel_size, filters, heads, enc_blocks, drop_prob, sent_len, word_embed, hidden_size):
        """
        @param conv_layers (int): Number of convolution layers in one Encoder Block.
        @param kernel_size (int): Kernel size of the convolution layers.
        @param filters (int): Number of filters for the convolution layers. 
        @param heads (int): Number of heads for multihead attention. 
        @param enc_blocks (int): Number of Encoder Blocks. 
        @param drop_prob (float): Probability of zero-ing out activations.
        @param sent_len (int): Input sentence size. 
        @param word_embed (int): Pretrained word vector size. 
        @param hidden_size (int): Number of features in the hidden state at each layer.
        """
        super(EmbeddingEncoderLayer, self).__init__()

        self.emb_enc = nn.ModuleList([
            EncoderBlock(conv_layers, kernel_size, 
                        filters, heads, drop_prob, sent_len, word_embed=word_embed, hidden_size=hidden_size),
            *(EncoderBlock(conv_layers, kernel_size,
                            filters, heads, drop_prob, sent_len, word_embed=hidden_size, hidden_size=hidden_size)
              for _ in range(enc_blocks-1))
        ])

    def forward(self, x, mask):
        """Encodes the word embeddings.
        @param x (torch.Tensor): Word embeddings. (batch_size, word_embed, sent_len)
        @returns x (torch.Tensor): Encoded Word embeddings. (batch_size, hidden_size, sent_len)
        """
        for layer in self.emb_enc:
            x = layer(x, mask)  # (batch_size, hidden_size, sent_len)
        return x  # (batch_size, hidden_size, sent_len)


class CQAttentionLayer(nn.Module):
    """Context Query Attention Layer.
    Takes 2 inputs: Context Encoded Embedding and Question Encoded Embedding. 
    Understood and Influenced from: https://github.com/tailerr/QANet-pytorch/
    """
    def __init__(self, hidden_size, drop_prob=0.2):
        """
        @param drop_prob (float): Probability of zero-ing out activations.
        """
        super(CQAttentionLayer, self).__init__()
        self.hidden_size = hidden_size

        w = torch.empty(hidden_size*3)  # (3*hidden_size)
        lim = 1/hidden_size
        nn.init.uniform_(w, -math.sqrt(lim), math.sqrt(lim))
        self.w = nn.Parameter(w)
        self.drop_prob = drop_prob

    def forward(self, context, question, c_mask, q_mask):
        """
        @param context (torch.Tensor): Encoded context embedding. (batch_size, hidden_size, c_len)
        @param question (torch.Tensor): Encoded question embedding. (batch_size, hidden_size, q_len)
        """
        if len(q_mask.shape) != 4:
            q_mask = q_mask.unsqueeze(1)  # (batch_size, 1, q_len)
        if len(c_mask.shape) != 4:
            c_mask = c_mask.unsqueeze(2) # (batch_size, c_len, 1) use for masked_softmax

        context = context.permute(0, 2, 1)  # (batch_size, c_len, hidden_size)
        query = question.permute(0, 2, 1)  # (batch_size, q_len, hidden_size)

        c_len = context.size(1)
        q_len = query.size(1)

        c = context.repeat(q_len, 1, 1, 1).permute([1, 0, 2, 3])  
        # (q_len, batch_size, c_len, hidden_size) --> (batch_size, q_len, c_len, hidden_size) 

        q = query.repeat(c_len, 1, 1, 1).permute([1, 2, 0, 3])  
        # (c_len, batch_size, q_len, hidden_size) --> (batch_size, q_len, c_len, hidden_size)
        
        cq = c*q  # (batch_size, q_len, c_len, hidden_size)
        s = (torch.cat((q, c, cq), dim=3) @ self.w).transpose(1, 2)
        # (batch_size, q_len, c_len, 3*hidden_size) --> (batch_size, q_len, c_len) --> (batch_size, c_len, q_len)

        s1 = masked_softmax(s, q_mask, dim=2)  # (batch_size, c_len, q_len)
        s2 = masked_softmax(s, c_mask, dim=1)  # (batch_size, c_len, q_len)

        a = torch.bmm(s1, query)  # (batch_size, c_len, hidden_size)
        l = torch.bmm(s1, s2.transpose(1, 2))  # (batch_size, c_len, c_len)
        b = torch.bmm(l, context)  # (batch_size, c_len, hidden_size)
        # * concat over hidden_state only
        output = torch.cat((context, a, context*a, context*b), dim=2)  # (batch_size, c_len, 4*hidden_size)
        return nn.functional.dropout(output, p=self.drop_prob).permute(0, 2, 1)  # (batch_size, 4*hidden_size, c_len)


class ModelEncoderLayer(nn.Module):
    """Model Encoder layer which encodes using convolution, self attention and feed forward network.
    Takes input from CQAttention Layer.
    """
    def __init__(self, conv_layers, kernel_size, filters, heads, enc_blocks, drop_prob, sent_len, word_embed, hidden_size):
        """
        @param conv_layers (int): Number of convolution layers in one Encoder Block.
        @param kernel_size (int): Kernel size of the convolution layers.
        @param filters (int): Number of filters for the convolution layers. 
        @param heads (int): Number of heads for multihead attention. 
        @param enc_blocks (int): Number of Encoder Blocks. 
        @param drop_prob (float): Probability of zero-ing out activations.
        @param sent_len (int): Input sentence size. 
        @param word_embed (int): Word vector size. 
        @param hidden_size (int): Number of features in the hidden state at each layer.
        """
        super(ModelEncoderLayer, self).__init__()

        self.model_enc = nn.ModuleList([
            EncoderBlock(conv_layers, kernel_size,
                                  filters, heads, drop_prob, sent_len, word_embed=word_embed, hidden_size=hidden_size),
            *(EncoderBlock(conv_layers, kernel_size,
                           filters, heads, drop_prob, sent_len, word_embed=hidden_size, hidden_size=hidden_size)
            for _ in range(enc_blocks-1))
        ])

    def forward(self, x, mask):
        """Encodes the word vectors.
        @param x (torch.Tensor): Input word vectors from CQAttention. (batch_size, , sent_len)
        @returns x (torch.Tensor): Encoded Word Vectors. (batch_size, hidden_size, sent_len)
        """
        for layer in self.model_enc:
            x = layer(x, mask)  # (batch_size, hidden_size, sent_len)
        return x


class OutputLayer(nn.Module):
    """Output Layer which outputs the probability distribution for the answer span in the context span.
    Takes inputs from 2 Model Encoder Layers.
    """
    def __init__(self, drop_prob, word_embed):
        """
        @param drop_prob (float): Probability of zero-ing out activations.
        @param word_embed (int): Word vector size. (128)
        """
        super(OutputLayer, self).__init__()
        self.ff = nn.Linear(2*word_embed, 1)

    def forward(self, input_1, input_2, mask):
        """Encodes the word embeddings.
        @param input_1 (torch.Tensor): Word vectors from first Model Encoder Layer. (batch_size, hidden_size, sent_len)
        @param input_2 (torch.Tensor): Word vectors from second Model Encoder Layer. (batch_size, hidden_size, sent_len)
        @returns p (torch.Tensor): Probability distribution for start/end token. (batch_size, sent_len)
        """
        x = torch.cat((input_1, input_2), dim=1)  # (batch_size, 2*hidden_size, sent_len)
        x = self.ff(x.permute(0, 2, 1)).permute(0, 2, 1)  # (batch_size, 1, sent_len)
        logits = x.squeeze()
        log_p = masked_softmax(logits, mask, log_softmax=True)
        return log_p  


# ---------------- Helper Layers ----------------------
class EncoderBlock(nn.Module):
    """Encoder Block used in Input Embedding Layer and Model Embedding Layer. 
    """
    def __init__(self, conv_layers, kernel_size, filters, heads, drop_prob, sent_len, word_embed, hidden_size):
        """
        @param conv_layers (int): Number of convolution layers in one Encoder Block.
        @param kernel_size (int): Kernel size of the convolution layers.
        @param filters (int): Number of filters for the convolution layers. 
        @param heads (int): Number of heads for multihead attention.
        @param drop_prob (float): Probability of zero-ing out activations.
        @param sent_len (int): Input sentence size. 
        @param word_embed (int): Word vector size. 
        @param hidden_size (int): Number of features in the hidden state at each layer.
        """
        super(EncoderBlock, self).__init__()

        self.pos_enc = PositionalEncoder(sent_len, word_embed)
        self.conv = nn.ModuleList([
            ConvBlock(word_embed=word_embed, sent_len=sent_len,
                        hidden_size=hidden_size, kernel_size=kernel_size),
            *(ConvBlock(word_embed=hidden_size, sent_len=sent_len,
                      hidden_size=hidden_size, kernel_size=kernel_size)
            for _ in range(conv_layers - 1))
        ])
        self.att = SelfAttentionBlock(hidden_size=hidden_size, sent_len=sent_len, heads=heads, drop_prob=drop_prob)
        self.ff = FeedForwardBlock(hidden_size=hidden_size, sent_len=sent_len)

    def forward(self, x, mask):
        """Encodes the word vectors.
        @param x (torch.Tensor): Word vectors. (batch_size, word_embed, sent_len)
        @returns x (torch.Tensor): Encoded Word embeddings. (batch_size, hidden_size, sent_len)
        """
        x = self.pos_enc(x)  # (batch_size, hidden_size, sent_len)
        for layer in self.conv:
            x = layer(x)  # (batch_size, hidden_size, sent_len)
        x = self.att(x, mask)  # (batch_size, hidden_size, sent_len)
        x = self.ff(x)  # (batch_size, hidden_size, sent_len)
        return x


class PositionalEncoder(nn.Module):
    """Generate positional encoding for a vector
    Args:
        length (int): length of the input sentence to be encoded
        d_model (int): dimention of the word vector
    Returns:
        torch.Tensor: positionaly encoded vector
    """

    def __init__(self, length, hidden_size):
        super(PositionalEncoder, self).__init__()

        f = torch.Tensor([10000 ** (-i / hidden_size) if i % 2 == 0 else -10000 **
                          ((1 - i) / hidden_size) for i in range(hidden_size)]).unsqueeze(dim=1)

        phase = torch.Tensor([0 if i % 2 == 0 else math.pi / 2 for i in range(hidden_size)]).unsqueeze(dim=1)
        pos = torch.arange(length).repeat(hidden_size, 1).to(torch.float)
        
        self.pos_encoding = nn.Parameter(torch.sin(torch.add(torch.mul(pos, f), phase)), requires_grad=False)

    def forward(self, x):
        return x + self.pos_encoding[0:x.size(1)]



class SelfAttention(nn.Module):
    """Self Attention used in Self Attention Block for Encoder Block.
    Refer to Attention is all you need paper to understand terminology
    """

    def __init__(self, hidden_size, heads, drop_prob):
        """
        @param word_embed (int): Word vector size. 
        @param sent_len (int): Input sentence size. 
        @param heads (int): Number of heads for multihead attention. 
        @param drop_prob (float): Probability of zero-ing out activations.
        """
        assert(hidden_size % heads == 0)
        super(SelfAttention, self).__init__()
        self.d_model = hidden_size
        self.h = heads
        self.d_v = self.d_model//heads

        self.W_q = nn.Linear(in_features=self.d_v, out_features=self.d_v, bias=False)
        self.W_k = nn.Linear(in_features=self.d_v, out_features=self.d_v, bias=False)
        self.W_v = nn.Linear(in_features=self.d_v, out_features=self.d_v, bias=False)

        self.linear = nn.Linear(in_features=self.d_model, out_features=self.d_model, bias=False)

    def forward(self, values, keys, query, mask=None):
        """
        @param x (torch.Tensor): Word vectors. (batch_size, hidden_size, sent_len)
        @returns x (torch.Tensor): Word vectors with self attention. (batch_size, hidden_size, sent_len)
        """
        if len(mask.shape) != 4:
            mask = mask.unsqueeze(1).unsqueeze(2) # (batch_size, 1, 1, sent_len)
        N = query.shape[0]  # batch_size
        value_len, key_len, query_len = values.shape[2], keys.shape[2], query.shape[2]

        values = values.permute(0, 2, 1)
        keys = keys.permute(0, 2, 1)
        query = query.permute(0, 2, 1)

        values = values.reshape(N, value_len, self.h, self.d_v)
        keys = keys.reshape(N, key_len, self.h, self.d_v)
        queries = query.reshape(N, query_len, self.h, self.d_v)

        values = self.W_v(values)
        keys = self.W_k(keys)
        queries = self.W_q(queries)

        energy = torch.einsum("nqhd,nkhd->nhqk", [queries, keys])
        attention = masked_softmax(energy / (self.d_model ** (1/2)), mask, dim=3)

        out = torch.einsum("nhql,nlhd->nqhd", [attention, values]).reshape(N, query_len, self.h*self.d_v)

        out = self.linear(out)
        return out  # (batch_size, sent_len, hidden_size)


# ---------------- Helper Residual Blocks ----------------------
class ConvBlock(nn.Module):
    """Conv Block used in Encoder Block.
    """
    def __init__(self, word_embed, sent_len, hidden_size, kernel_size):
        """
        @param word_embed (int): Word vector size. 
        @param sent_len (int): Input sentence size. 
        @param out_channels (int): Number of output features.
        @param kernel_size (int): Kernel size of the convolution layers.
        """
        super(ConvBlock, self).__init__()
        self.word_embed = word_embed
        self.hidden_size = hidden_size

        self.layer_norm = nn.LayerNorm([word_embed, sent_len])
        self.conv = DepthwiseSeparableConv(word_embed, hidden_size, kernel_size, padding=kernel_size//2)
        self.w_s = nn.Linear(word_embed, hidden_size) # linear projection

    def forward(self, x):
        """
        @param x (torch.Tensor): Word vectors. (batch_size, word_embed, sent_len)
        @returns x (torch.Tensor): Word vectors. (batch_size, hidden_size, sent_len)

        Shortcut Connections based on the paper:
        "Deep Residual Learning for Image Recognition"
        by Kaiming He, Xiangyu Zhang, Shaoqing Ren, Jian Sun
        (https://arxiv.org/abs/1512.03385)

        For linear projection before Shortcut Connection:
            Refer Equation 2 (Section 3.2) in https://arxiv.org/pdf/1512.03385.pdf 

        """
        x_l = self.layer_norm(x)  # (batch_size, word_embed, sent_len)
        if(self.word_embed != self.hidden_size): 
            x = self.w_s(x.permute(0, 2, 1)).permute(0, 2, 1)  # (batch_size, hidden_size, sent_len)
        x = x + self.conv(x_l)  # (batch_size, hidden_size, sent_len)
        return x


class SelfAttentionBlock(nn.Module):
    """Self Attention Block used in Encoder Block.
    """
    def __init__(self, hidden_size, sent_len, heads, drop_prob):
        """
        @param word_embed (int): Word vector size. 
        @param sent_len (int): Input sentence size. 
        @param heads (int): Number of heads for multihead attention.
        @param drop_prob (float): Probability of zero-ing out activations.
        """
        super(SelfAttentionBlock, self).__init__()

        self.layer_norm = nn.LayerNorm([hidden_size, sent_len])
        self.self_attn = SelfAttention(hidden_size, heads, drop_prob)

    def forward(self, x, mask):
        """
        @param x (torch.Tensor): Word vectors. (batch_size, hidden_size, sent_len)
        @returns x (torch.Tensor): Word vectors with self attention. (batch_size, hidden_size, sent_len)
        """
        a = self.layer_norm(x)  # (batch_size, hidden_size, sent_len)
        att = self.self_attn(a, a, a, mask=mask)
        att = att.permute(0, 2, 1)  # (batch_size, hidden_size, sent_len)

        return x + att

class FeedForwardBlock(nn.Module):
    """Feed Forward Block used in Encoder Block.
    """

    def __init__(self, hidden_size, sent_len):
        """
        @param word_embed (int): Word vector size. 
        @param sent_len (int): Input sentence size.
        """
        super(FeedForwardBlock, self).__init__()

        self.layer_norm = nn.LayerNorm([hidden_size, sent_len])
        self.ff = nn.Sequential(
            nn.Linear(in_features=hidden_size, out_features=hidden_size),
            nn.ReLU()
        )

    def forward(self, x):
        """
        @param x (torch.Tensor): Word vectors. (batch_size, hidden_size, sent_len)
        @returns x (torch.Tensor): Word vectors. (batch_size, hidden_size, sent_len)
        """
        x_l = self.layer_norm(x)  # (batch_size, hidden_size, sent_len)
        x = x + self.ff(x_l.permute(0, 2, 1)).permute(0, 2, 1)  # (batch_size, hidden_size, sent_len)
        return x

# ----------------- Additional Blocks ---------------------------

class HighwayEncoder(nn.Module):
    """Encode an input sequence using a highway network.

    Based on the paper:
    "Highway Networks"
    by Rupesh Kumar Srivastava, Klaus Greff, Jürgen Schmidhuber
    (https://arxiv.org/abs/1505.00387).

    Args:
        num_layers (int): Number of layers in the highway encoder.
        hidden_size (int): Size of hidden activations.
    """
    def __init__(self, num_layers, hidden_size):
        super(HighwayEncoder, self).__init__()
        self.transforms = nn.ModuleList([nn.Linear(hidden_size, hidden_size)
                                         for _ in range(num_layers)])
        self.gates = nn.ModuleList([nn.Linear(hidden_size, hidden_size)
                                    for _ in range(num_layers)])

    def forward(self, x):
        for gate, transform in zip(self.gates, self.transforms):
            # Shapes of g, t, and x are all (batch_size, seq_len, hidden_size)
            g = torch.sigmoid(gate(x))
            t = F.relu(transform(x))
            x = g * t + (1 - g) * x

        return x

class DepthwiseSeparableConv(nn.Module):

    def __init__(self, word_embed, hidden_size, kernel_size, padding, bias=True):
        super().__init__()
        self.depthwise_conv = nn.Conv1d(in_channels=word_embed, out_channels=word_embed, kernel_size=kernel_size, groups=word_embed, padding=kernel_size // 2, bias=False)
        self.pointwise_conv = nn.Conv1d(in_channels=word_embed, out_channels=hidden_size, kernel_size=1, padding=0, bias=bias)

    def forward(self, x):
        return self.pointwise_conv(self.depthwise_conv(x))
