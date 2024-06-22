# coding=utf-8
# Copyright 2022 SHI Labs and The HuggingFace Inc. team. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""PyTorch Neighborhood Attention Transformer model."""

import math
from dataclasses import dataclass
from typing import Optional, Tuple, Union

import mindspore as ms
from mindspore import nn, ops
from mindspore.common.initializer import initializer, Normal
from ...activations import ACT2FN
from ...modeling_outputs import BackboneOutput
from ...modeling_utils import PreTrainedModel
from ...ms_utils import find_pruneable_heads_and_indices, prune_linear_layer
from ....utils import (
    ModelOutput,
    OptionalDependencyNotAvailable,
    logging,
    requires_backends,
)
from ...backbone_utils import BackboneMixin
from .configuration_nat import NatConfig

# if is_natten_available():
#     from natten.functional import natten2dav, natten2dqkrpb
# else:

#     def natten2dqkrpb(*args, **kwargs):
#         raise OptionalDependencyNotAvailable()

#     def natten2dav(*args, **kwargs):
#         raise OptionalDependencyNotAvailable()


def natten2dqkrpb(*args, **kwargs):
    raise OptionalDependencyNotAvailable()


def natten2dav(*args, **kwargs):
    raise OptionalDependencyNotAvailable()


logger = logging.get_logger(__name__)

# General docstring
_CONFIG_FOR_DOC = "NatConfig"

# Base docstring
_CHECKPOINT_FOR_DOC = "shi-labs/nat-mini-in1k-224"
_EXPECTED_OUTPUT_SHAPE = [1, 7, 7, 512]

# Image classification docstring
_IMAGE_CLASS_CHECKPOINT = "shi-labs/nat-mini-in1k-224"
_IMAGE_CLASS_EXPECTED_OUTPUT = "tiger cat"


# drop_path and NatDropPath are from the timm library.


@dataclass
class NatEncoderOutput(ModelOutput):
    """
    Nat encoder's outputs, with potential hidden states and attentions.

    Args:
        last_hidden_state (`ms.Tensor` of shape `(batch_size, sequence_length, hidden_size)`):
            Sequence of hidden-states at the output of the last layer of the model.
        hidden_states (`tuple(ms.Tensor)`, *optional*, returned when `output_hidden_states=True` is passed or when `config.output_hidden_states=True`):
            Tuple of `ms.Tensor` (one for the output of the embeddings + one for the output of each stage) of
            shape `(batch_size, sequence_length, hidden_size)`.

            Hidden-states of the model at the output of each layer plus the initial embedding outputs.
        attentions (`tuple(ms.Tensor)`, *optional*, returned when `output_attentions=True` is passed or when `config.output_attentions=True`):
            Tuple of `ms.Tensor` (one for each stage) of shape `(batch_size, num_heads, sequence_length,
            sequence_length)`.

            Attentions weights after the attention softmax, used to compute the weighted average in the self-attention
            heads.
        reshaped_hidden_states (`tuple(ms.Tensor)`, *optional*, returned when `output_hidden_states=True` is passed or when `config.output_hidden_states=True`):
            Tuple of `ms.Tensor` (one for the output of the embeddings + one for the output of each stage) of
            shape `(batch_size, hidden_size, height, width)`.

            Hidden-states of the model at the output of each layer plus the initial embedding outputs reshaped to
            include the spatial dimensions.
    """

    last_hidden_state: ms.Tensor = None
    hidden_states: Optional[Tuple[ms.Tensor, ...]] = None
    attentions: Optional[Tuple[ms.Tensor, ...]] = None
    reshaped_hidden_states: Optional[Tuple[ms.Tensor, ...]] = None


@dataclass
class NatModelOutput(ModelOutput):
    """
    Nat model's outputs that also contains a pooling of the last hidden states.

    Args:
        last_hidden_state (`ms.Tensor` of shape `(batch_size, sequence_length, hidden_size)`):
            Sequence of hidden-states at the output of the last layer of the model.
        pooler_output (`ms.Tensor` of shape `(batch_size, hidden_size)`, *optional*, returned when `add_pooling_layer=True` is passed):
            Average pooling of the last layer hidden-state.
        hidden_states (`tuple(ms.Tensor)`, *optional*, returned when `output_hidden_states=True` is passed or when `config.output_hidden_states=True`):
            Tuple of `ms.Tensor` (one for the output of the embeddings + one for the output of each stage) of
            shape `(batch_size, sequence_length, hidden_size)`.

            Hidden-states of the model at the output of each layer plus the initial embedding outputs.
        attentions (`tuple(ms.Tensor)`, *optional*, returned when `output_attentions=True` is passed or when `config.output_attentions=True`):
            Tuple of `ms.Tensor` (one for each stage) of shape `(batch_size, num_heads, sequence_length,
            sequence_length)`.

            Attentions weights after the attention softmax, used to compute the weighted average in the self-attention
            heads.
        reshaped_hidden_states (`tuple(ms.Tensor)`, *optional*, returned when `output_hidden_states=True` is passed or when `config.output_hidden_states=True`):
            Tuple of `ms.Tensor` (one for the output of the embeddings + one for the output of each stage) of
            shape `(batch_size, hidden_size, height, width)`.

            Hidden-states of the model at the output of each layer plus the initial embedding outputs reshaped to
            include the spatial dimensions.
    """

    last_hidden_state: ms.Tensor = None
    pooler_output: Optional[ms.Tensor] = None
    hidden_states: Optional[Tuple[ms.Tensor, ...]] = None
    attentions: Optional[Tuple[ms.Tensor, ...]] = None
    reshaped_hidden_states: Optional[Tuple[ms.Tensor, ...]] = None


@dataclass
class NatImageClassifierOutput(ModelOutput):
    """
    Nat outputs for image classification.

    Args:
        loss (`ms.Tensor` of shape `(1,)`, *optional*, returned when `labels` is provided):
            Classification (or regression if config.num_labels==1) loss.
        logits (`ms.Tensor` of shape `(batch_size, config.num_labels)`):
            Classification (or regression if config.num_labels==1) scores (before SoftMax).
        hidden_states (`tuple(ms.Tensor)`, *optional*, returned when `output_hidden_states=True` is passed or when `config.output_hidden_states=True`):
            Tuple of `ms.Tensor` (one for the output of the embeddings + one for the output of each stage) of
            shape `(batch_size, sequence_length, hidden_size)`.

            Hidden-states of the model at the output of each layer plus the initial embedding outputs.
        attentions (`tuple(ms.Tensor)`, *optional*, returned when `output_attentions=True` is passed or when `config.output_attentions=True`):
            Tuple of `ms.Tensor` (one for each stage) of shape `(batch_size, num_heads, sequence_length,
            sequence_length)`.

            Attentions weights after the attention softmax, used to compute the weighted average in the self-attention
            heads.
        reshaped_hidden_states (`tuple(ms.Tensor)`, *optional*, returned when `output_hidden_states=True` is passed or when `config.output_hidden_states=True`):
            Tuple of `ms.Tensor` (one for the output of the embeddings + one for the output of each stage) of
            shape `(batch_size, hidden_size, height, width)`.

            Hidden-states of the model at the output of each layer plus the initial embedding outputs reshaped to
            include the spatial dimensions.
    """

    loss: Optional[ms.Tensor] = None
    logits: ms.Tensor = None
    hidden_states: Optional[Tuple[ms.Tensor, ...]] = None
    attentions: Optional[Tuple[ms.Tensor, ...]] = None
    reshaped_hidden_states: Optional[Tuple[ms.Tensor, ...]] = None


class NatEmbeddings(nn.Cell):
    """
    Construct the patch and position embeddings.
    """

    def __init__(self, config):
        super().__init__()

        self.patch_embeddings = NatPatchEmbeddings(config)

        self.norm = nn.LayerNorm([config.embed_dim])
        self.dropout = nn.Dropout(config.hidden_dropout_prob)

    def construct(self, pixel_values: Optional[ms.Tensor]) -> Tuple[ms.Tensor]:
        embeddings = self.patch_embeddings(pixel_values)
        embeddings = self.norm(embeddings)

        embeddings = self.dropout(embeddings)

        return embeddings


class NatPatchEmbeddings(nn.Cell):
    """
    This class turns `pixel_values` of shape `(batch_size, num_channels, height, width)` into the initial
    `hidden_states` (patch embeddings) of shape `(batch_size, height, width, hidden_size)` to be consumed by a
    Transformer.
    """

    def __init__(self, config):
        super().__init__()
        patch_size = config.patch_size
        num_channels, hidden_size = config.num_channels, config.embed_dim
        self.num_channels = num_channels

        if patch_size == 4:
            pass
        else:
            # TODO: Support arbitrary patch sizes.
            raise ValueError("Dinat only supports patch size of 4 at the moment.")

        self.projection = nn.SequentialCell(
            nn.Conv2d(
                self.num_channels,
                hidden_size // 2,
                kernel_size=3,
                stride=2,
                padding=1,
                pad_mode="pad",
            ),
            nn.Conv2d(
                hidden_size // 2,
                hidden_size,
                kernel_size=3,
                stride=2,
                padding=1,
                pad_mode="pad",
            ),
        )

    def construct(self, pixel_values: Optional[ms.Tensor]) -> ms.Tensor:
        _, num_channels, height, width = pixel_values.shape
        if num_channels != self.num_channels:
            raise ValueError(
                "Make sure that the channel dimension of the pixel values match with the one set in the configuration."
            )
        embeddings = self.projection(pixel_values)
        embeddings = embeddings.permute(0, 2, 3, 1)

        return embeddings


class NatDownsampler(nn.Cell):
    """
    Convolutional Downsampling Layer.

    Args:
        dim (`int`):
            Number of input channels.
        norm_layer (`nn.Cell`, *optional*, defaults to `nn.LayerNorm`):
            Normalization layer class.
    """

    def __init__(self, dim: int, norm_layer: nn.Cell = nn.LayerNorm) -> None:
        super().__init__()
        self.dim = dim
        self.reduction = nn.Conv2d(
            dim,
            2 * dim,
            kernel_size=3,
            stride=2,
            padding=1,
            has_bias=False,
            pad_mode="pad",
        )
        self.norm = norm_layer(2 * dim)

    def construct(self, input_feature: ms.Tensor) -> ms.Tensor:
        input_feature = self.reduction(input_feature.permute(0, 3, 1, 2)).permute(
            0, 2, 3, 1
        )
        input_feature = self.norm(input_feature)
        return input_feature


def drop_path(
    input: ms.Tensor, drop_prob: float = 0.0, training: bool = False
) -> ms.Tensor:
    """
    Drop paths (Stochastic Depth) per sample (when applied in main path of residual blocks).

    Comment by Ross Wightman: This is the same as the DropConnect impl I created for EfficientNet, etc networks,
    however, the original name is misleading as 'Drop Connect' is a different form of dropout in a separate paper...
    See discussion: https://github.com/tensorflow/tpu/issues/494#issuecomment-532968956 ... I've opted for changing the
    layer and argument names to 'drop path' rather than mix DropConnect as a layer name and use 'survival rate' as the
    argument.
    """
    if drop_prob == 0.0 or not training:
        return input
    keep_prob = 1 - drop_prob
    shape = (input.shape[0],) + (1,) * (
        input.ndim - 1
    )  # work with diff dim tensors, not just 2D ConvNets
    random_tensor = keep_prob + ops.rand(shape, dtype=input.dtype)
    random_tensor.floor_()  # binarize
    output = input.div(keep_prob) * random_tensor
    return output


class NatDropPath(nn.Cell):
    """Drop paths (Stochastic Depth) per sample (when applied in main path of residual blocks)."""

    def __init__(self, drop_prob: Optional[float] = None) -> None:
        super().__init__()
        self.drop_prob = drop_prob

    def construct(self, hidden_states: ms.Tensor) -> ms.Tensor:
        return drop_path(hidden_states, self.drop_prob, self.training)

    def extra_repr(self) -> str:
        return "p={}".format(self.drop_prob)


class NeighborhoodAttention(nn.Cell):
    def __init__(self, config, dim, num_heads, kernel_size):
        super().__init__()
        if dim % num_heads != 0:
            raise ValueError(
                f"The hidden size ({dim}) is not a multiple of the number of attention heads ({num_heads})"
            )

        self.num_attention_heads = num_heads
        self.attention_head_size = int(dim / num_heads)
        self.all_head_size = self.num_attention_heads * self.attention_head_size
        self.kernel_size = kernel_size

        # rpb is learnable relative positional biases; same concept is used Swin.
        self.rpb = ms.Parameter(
            ops.zeros(num_heads, (2 * self.kernel_size - 1), (2 * self.kernel_size - 1))
        )

        self.query = nn.Dense(
            self.all_head_size, self.all_head_size, has_bias=config.qkv_bias
        )
        self.key = nn.Dense(
            self.all_head_size, self.all_head_size, has_bias=config.qkv_bias
        )
        self.value = nn.Dense(
            self.all_head_size, self.all_head_size, has_bias=config.qkv_bias
        )

        self.dropout = nn.Dropout(config.attention_probs_dropout_prob)

    def transpose_for_scores(self, x):
        new_x_shape = x.shape[:-1] + (
            self.num_attention_heads,
            self.attention_head_size,
        )
        x = x.view(new_x_shape)
        return x.permute(0, 3, 1, 2, 4)

    def construct(
        self,
        hidden_states: ms.Tensor,
        output_attentions: Optional[bool] = False,
    ) -> Tuple[ms.Tensor]:
        query_layer = self.transpose_for_scores(self.query(hidden_states))
        key_layer = self.transpose_for_scores(self.key(hidden_states))
        value_layer = self.transpose_for_scores(self.value(hidden_states))

        # Apply the scale factor before computing attention weights. It's usually more efficient because
        # attention weights are typically a bigger tensor compared to query.
        # It gives identical results because scalars are commutable in matrix multiplication.
        query_layer = query_layer / math.sqrt(self.attention_head_size)

        # Compute NA between "query" and "key" to get the raw attention scores, and add relative positional biases.
        attention_scores = natten2dqkrpb(
            query_layer, key_layer, self.rpb, self.kernel_size, 1
        )

        # Normalize the attention scores to probabilities.
        attention_probs = ops.softmax(attention_scores, axis=-1)

        # This is actually dropping out entire tokens to attend to, which might
        # seem a bit unusual, but is taken from the original Transformer paper.
        attention_probs = self.dropout(attention_probs)

        context_layer = natten2dav(attention_probs, value_layer, self.kernel_size, 1)
        context_layer = context_layer.permute(0, 2, 3, 1, 4).contiguous()
        new_context_layer_shape = context_layer.shape[:-2] + (self.all_head_size,)
        context_layer = context_layer.view(new_context_layer_shape)

        outputs = (
            (context_layer, attention_probs) if output_attentions else (context_layer,)
        )

        return outputs


class NeighborhoodAttentionOutput(nn.Cell):
    def __init__(self, config, dim):
        super().__init__()
        self.dense = nn.Dense(dim, dim)
        self.dropout = nn.Dropout(config.attention_probs_dropout_prob)

    def construct(self, hidden_states: ms.Tensor, input_tensor: ms.Tensor) -> ms.Tensor:
        hidden_states = self.dense(hidden_states)
        hidden_states = self.dropout(hidden_states)

        return hidden_states


class NeighborhoodAttentionModule(nn.Cell):
    def __init__(self, config, dim, num_heads, kernel_size):
        super().__init__()
        self.self = NeighborhoodAttention(config, dim, num_heads, kernel_size)
        self.output = NeighborhoodAttentionOutput(config, dim)
        self.pruned_heads = set()

    def prune_heads(self, heads):
        if len(heads) == 0:
            return
        heads, index = find_pruneable_heads_and_indices(
            heads,
            self.self.num_attention_heads,
            self.self.attention_head_size,
            self.pruned_heads,
        )

        # Prune linear layers
        self.self.query = prune_linear_layer(self.self.query, index)
        self.self.key = prune_linear_layer(self.self.key, index)
        self.self.value = prune_linear_layer(self.self.value, index)
        self.output.dense = prune_linear_layer(self.output.dense, index, axis=1)

        # Update hyper params and store pruned heads
        self.self.num_attention_heads = self.self.num_attention_heads - len(heads)
        self.self.all_head_size = (
            self.self.attention_head_size * self.self.num_attention_heads
        )
        self.pruned_heads = self.pruned_heads.union(heads)

    def construct(
        self,
        hidden_states: ms.Tensor,
        output_attentions: Optional[bool] = False,
    ) -> Tuple[ms.Tensor]:
        self_outputs = self.self(hidden_states, output_attentions)
        attention_output = self.output(self_outputs[0], hidden_states)
        outputs = (attention_output,) + self_outputs[
            1:
        ]  # add attentions if we output them
        return outputs


class NatIntermediate(nn.Cell):
    def __init__(self, config, dim):
        super().__init__()
        self.dense = nn.Dense(dim, int(config.mlp_ratio * dim))
        if isinstance(config.hidden_act, str):
            self.intermediate_act_fn = ACT2FN[config.hidden_act]
        else:
            self.intermediate_act_fn = config.hidden_act

    def construct(self, hidden_states: ms.Tensor) -> ms.Tensor:
        hidden_states = self.dense(hidden_states)
        hidden_states = self.intermediate_act_fn(hidden_states)
        return hidden_states


class NatOutput(nn.Cell):
    def __init__(self, config, dim):
        super().__init__()
        self.dense = nn.Dense(int(config.mlp_ratio * dim), dim)
        self.dropout = nn.Dropout(config.hidden_dropout_prob)

    def construct(self, hidden_states: ms.Tensor) -> ms.Tensor:
        hidden_states = self.dense(hidden_states)
        hidden_states = self.dropout(hidden_states)
        return hidden_states


class NatLayer(nn.Cell):
    def __init__(self, config, dim, num_heads, drop_path_rate=0.0):
        super().__init__()
        self.chunk_size_feed_forward = config.chunk_size_feed_forward
        self.kernel_size = config.kernel_size
        self.layernorm_before = nn.LayerNorm([dim], epsilon=config.layer_norm_eps)
        self.attention = NeighborhoodAttentionModule(
            config, dim, num_heads, kernel_size=self.kernel_size
        )
        self.drop_path = (
            NatDropPath(drop_path_rate) if drop_path_rate > 0.0 else nn.Identity()
        )
        self.layernorm_after = nn.LayerNorm([dim], epsilon=config.layer_norm_eps)
        self.intermediate = NatIntermediate(config, dim)
        self.output = NatOutput(config, dim)
        self.layer_scale_parameters = (
            ms.Parameter(
                config.layer_scale_init_value * ops.ones((2, dim)), requires_grad=True
            )
            if config.layer_scale_init_value > 0
            else None
        )

    def maybe_pad(self, hidden_states, height, width):
        window_size = self.kernel_size
        pad_values = (0, 0, 0, 0, 0, 0)
        if height < window_size or width < window_size:
            pad_l = pad_t = 0
            pad_r = max(0, window_size - width)
            pad_b = max(0, window_size - height)
            pad_values = (0, 0, pad_l, pad_r, pad_t, pad_b)
            hidden_states = ops.pad(hidden_states, pad_values)
        return hidden_states, pad_values

    def construct(
        self,
        hidden_states: ms.Tensor,
        output_attentions: Optional[bool] = False,
    ) -> Tuple[ms.Tensor, ms.Tensor]:
        batch_size, height, width, channels = hidden_states.shape
        shortcut = hidden_states

        hidden_states = self.layernorm_before(hidden_states)
        # pad hidden_states if they are smaller than kernel size
        hidden_states, pad_values = self.maybe_pad(hidden_states, height, width)

        _, height_pad, width_pad, _ = hidden_states.shape

        attention_outputs = self.attention(
            hidden_states, output_attentions=output_attentions
        )

        attention_output = attention_outputs[0]

        was_padded = pad_values[3] > 0 or pad_values[5] > 0
        if was_padded:
            attention_output = attention_output[:, :height, :width, :].contiguous()

        if self.layer_scale_parameters is not None:
            attention_output = self.layer_scale_parameters[0] * attention_output

        hidden_states = shortcut + self.drop_path(attention_output)

        layer_output = self.layernorm_after(hidden_states)
        layer_output = self.output(self.intermediate(layer_output))

        if self.layer_scale_parameters is not None:
            layer_output = self.layer_scale_parameters[1] * layer_output

        layer_output = hidden_states + self.drop_path(layer_output)

        layer_outputs = (
            (layer_output, attention_outputs[1])
            if output_attentions
            else (layer_output,)
        )
        return layer_outputs


class NatStage(nn.Cell):
    def __init__(self, config, dim, depth, num_heads, drop_path_rate, downsample):
        super().__init__()
        self.config = config
        self.dim = dim
        self.layers = nn.CellList(
            [
                NatLayer(
                    config=config,
                    dim=dim,
                    num_heads=num_heads,
                    drop_path_rate=drop_path_rate[i],
                )
                for i in range(depth)
            ]
        )

        # patch merging layer
        if downsample is not None:
            self.downsample = downsample(dim=dim, norm_layer=nn.LayerNorm)
        else:
            self.downsample = None

        self.pointing = False

    def construct(
        self,
        hidden_states: ms.Tensor,
        output_attentions: Optional[bool] = False,
    ) -> Tuple[ms.Tensor]:
        _, height, width, _ = hidden_states.shape
        for i, layer_module in enumerate(self.layers):
            layer_outputs = layer_module(hidden_states, output_attentions)
            hidden_states = layer_outputs[0]

        hidden_states_before_downsampling = hidden_states
        if self.downsample is not None:
            hidden_states = self.downsample(hidden_states_before_downsampling)

        stage_outputs = (hidden_states, hidden_states_before_downsampling)

        if output_attentions:
            stage_outputs += layer_outputs[1:]
        return stage_outputs


class NatEncoder(nn.Cell):
    def __init__(self, config):
        super().__init__()
        self.num_levels = len(config.depths)
        self.config = config
        dpr = [
            x.item() for x in ops.linspace(0, config.drop_path_rate, sum(config.depths))
        ]
        self.levels = nn.CellList(
            [
                NatStage(
                    config=config,
                    dim=int(config.embed_dim * 2**i_layer),
                    depth=config.depths[i_layer],
                    num_heads=config.num_heads[i_layer],
                    drop_path_rate=dpr[
                        sum(config.depths[:i_layer]) : sum(config.depths[: i_layer + 1])
                    ],
                    downsample=(
                        NatDownsampler if (i_layer < self.num_levels - 1) else None
                    ),
                )
                for i_layer in range(self.num_levels)
            ]
        )

    def construct(
        self,
        hidden_states: ms.Tensor,
        output_attentions: Optional[bool] = False,
        output_hidden_states: Optional[bool] = False,
        output_hidden_states_before_downsampling: Optional[bool] = False,
        return_dict: Optional[bool] = True,
    ) -> Union[Tuple, NatEncoderOutput]:
        all_hidden_states = () if output_hidden_states else None
        all_reshaped_hidden_states = () if output_hidden_states else None
        all_self_attentions = () if output_attentions else None

        if output_hidden_states:
            # rearrange b h w c -> b c h w
            reshaped_hidden_state = hidden_states.permute(0, 3, 1, 2)
            all_hidden_states += (hidden_states,)
            all_reshaped_hidden_states += (reshaped_hidden_state,)

        for i, layer_module in enumerate(self.levels):
            layer_outputs = layer_module(hidden_states, output_attentions)

            hidden_states = layer_outputs[0]
            hidden_states_before_downsampling = layer_outputs[1]

            if output_hidden_states and output_hidden_states_before_downsampling:
                # rearrange b h w c -> b c h w
                reshaped_hidden_state = hidden_states_before_downsampling.permute(
                    0, 3, 1, 2
                )
                all_hidden_states += (hidden_states_before_downsampling,)
                all_reshaped_hidden_states += (reshaped_hidden_state,)
            elif output_hidden_states and not output_hidden_states_before_downsampling:
                # rearrange b h w c -> b c h w
                reshaped_hidden_state = hidden_states.permute(0, 3, 1, 2)
                all_hidden_states += (hidden_states,)
                all_reshaped_hidden_states += (reshaped_hidden_state,)

            if output_attentions:
                all_self_attentions += layer_outputs[2:]

        if not return_dict:
            return tuple(
                v
                for v in [hidden_states, all_hidden_states, all_self_attentions]
                if v is not None
            )

        return NatEncoderOutput(
            last_hidden_state=hidden_states,
            hidden_states=all_hidden_states,
            attentions=all_self_attentions,
            reshaped_hidden_states=all_reshaped_hidden_states,
        )


class NatPreTrainedModel(PreTrainedModel):
    """
    An abstract class to handle weights initialization and a simple interface for downloading and loading pretrained
    models.
    """

    config_class = NatConfig
    base_model_prefix = "nat"
    main_input_name = "pixel_values"

    def _init_weights(self, module):
        """Initialize the weights"""
        if isinstance(module, (nn.Dense, nn.Conv2d)):
            # Slightly different from the TF version which uses truncated_normal for initialization
            # cf https://github.com/pytorch/pytorch/pull/5617
            module.weight.set_data(
                initializer(
                    Normal(sigma=self.config.initializer_range, mean=0.0),
                    module.weight.shape,
                    module.weight.dtype,
                )
            )
            if module.bias is not None:
                module.bias.set_data(
                    initializer(
                        "zeros",
                        module.bias.shape,
                        module.bias.dtype,
                    )
                )
        elif isinstance(module, nn.LayerNorm):
            module.bias.set_data(
                initializer(
                    "zeros",
                    module.bias.shape,
                    module.bias.dtype,
                )
            )
            module.weight.set_data(
                initializer(
                    "ones",
                    module.weight.shape,
                    module.weight.dtype,
                )
            )


NAT_START_DOCSTRING = r"""
    This model is a PyTorch [torch.nn.Cell](https://pytorch.org/docs/stable/nn.html#torch.nn.Cell) sub-class. Use
    it as a regular PyTorch Module and refer to the PyTorch documentation for all matter related to general usage and
    behavior.

    Parameters:
        config ([`NatConfig`]): Model configuration class with all the parameters of the model.
            Initializing with a config file does not load the weights associated with the model, only the
            configuration. Check out the [`~PreTrainedModel.from_pretrained`] method to load the model weights.
"""


NAT_INPUTS_DOCSTRING = r"""
    Args:
        pixel_values (`ms.Tensor` of shape `(batch_size, num_channels, height, width)`):
            Pixel values. Pixel values can be obtained using [`AutoImageProcessor`]. See [`ViTImageProcessor.__call__`]
            for details.

        output_attentions (`bool`, *optional*):
            Whether or not to return the attentions tensors of all attention layers. See `attentions` under returned
            tensors for more detail.
        output_hidden_states (`bool`, *optional*):
            Whether or not to return the hidden states of all layers. See `hidden_states` under returned tensors for
            more detail.
        return_dict (`bool`, *optional*):
            Whether or not to return a [`~utils.ModelOutput`] instead of a plain tuple.
"""


class NatModel(NatPreTrainedModel):
    def __init__(self, config, add_pooling_layer=True):
        super().__init__(config)

        requires_backends(self, ["mindspore"])

        self.config = config
        self.num_levels = len(config.depths)
        self.num_features = int(config.embed_dim * 2 ** (self.num_levels - 1))

        self.embeddings = NatEmbeddings(config)
        self.encoder = NatEncoder(config)

        self.layernorm = nn.LayerNorm(
            [self.num_features], epsilon=config.layer_norm_eps
        )
        self.pooler = nn.AdaptiveAvgPool1d(1) if add_pooling_layer else None

        # Initialize weights and apply final processing
        self.post_init()

    def get_input_embeddings(self):
        return self.embeddings.patch_embeddings

    def _prune_heads(self, heads_to_prune):
        """
        Prunes heads of the model. heads_to_prune: dict of {layer_num: list of heads to prune in this layer} See base
        class PreTrainedModel
        """
        for layer, heads in heads_to_prune.items():
            self.encoder.layer[layer].attention.prune_heads(heads)

    def construct(
        self,
        pixel_values: Optional[ms.Tensor] = None,
        output_attentions: Optional[bool] = None,
        output_hidden_states: Optional[bool] = None,
        return_dict: Optional[bool] = None,
    ) -> Union[Tuple, NatModelOutput]:
        output_attentions = (
            output_attentions
            if output_attentions is not None
            else self.config.output_attentions
        )
        output_hidden_states = (
            output_hidden_states
            if output_hidden_states is not None
            else self.config.output_hidden_states
        )
        return_dict = (
            return_dict if return_dict is not None else self.config.use_return_dict
        )

        if pixel_values is None:
            raise ValueError("You have to specify pixel_values")

        embedding_output = self.embeddings(pixel_values)

        encoder_outputs = self.encoder(
            embedding_output,
            output_attentions=output_attentions,
            output_hidden_states=output_hidden_states,
            return_dict=return_dict,
        )

        sequence_output = encoder_outputs[0]
        sequence_output = self.layernorm(sequence_output)

        pooled_output = None
        if self.pooler is not None:
            pooled_output = self.pooler(sequence_output.flatten(1, 2).swapaxes(1, 2))
            pooled_output = ops.flatten(pooled_output, 1)

        if not return_dict:
            output = (sequence_output, pooled_output) + encoder_outputs[1:]

            return output

        return NatModelOutput(
            last_hidden_state=sequence_output,
            pooler_output=pooled_output,
            hidden_states=encoder_outputs.hidden_states,
            attentions=encoder_outputs.attentions,
            reshaped_hidden_states=encoder_outputs.reshaped_hidden_states,
        )


class NatForImageClassification(NatPreTrainedModel):
    def __init__(self, config):
        super().__init__(config)

        requires_backends(self, ["mindspore"])

        self.num_labels = config.num_labels
        self.nat = NatModel(config)

        # Classifier head
        self.classifier = (
            nn.Dense(self.nat.num_features, config.num_labels)
            if config.num_labels > 0
            else nn.Identity()
        )

        # Initialize weights and apply final processing
        self.post_init()

    def construct(
        self,
        pixel_values: Optional[ms.Tensor] = None,
        labels: Optional[ms.Tensor] = None,
        output_attentions: Optional[bool] = None,
        output_hidden_states: Optional[bool] = None,
        return_dict: Optional[bool] = None,
    ) -> Union[Tuple, NatImageClassifierOutput]:
        r"""
        labels (`ms.Tensor` of shape `(batch_size,)`, *optional*):
            Labels for computing the image classification/regression loss. Indices should be in `[0, ...,
            config.num_labels - 1]`. If `config.num_labels == 1` a regression loss is computed (Mean-Square loss), If
            `config.num_labels > 1` a classification loss is computed (Cross-Entropy).
        """
        return_dict = (
            return_dict if return_dict is not None else self.config.use_return_dict
        )

        outputs = self.nat(
            pixel_values,
            output_attentions=output_attentions,
            output_hidden_states=output_hidden_states,
            return_dict=return_dict,
        )

        pooled_output = outputs[1]

        logits = self.classifier(pooled_output)

        loss = None
        if labels is not None:
            if self.config.problem_type is None:
                if self.num_labels == 1:
                    self.config.problem_type = "regression"
                elif self.num_labels > 1 and (
                    labels.dtype == ms.int64 or labels.dtype == ms.int32
                ):
                    self.config.problem_type = "single_label_classification"
                else:
                    self.config.problem_type = "multi_label_classification"

            if self.config.problem_type == "regression":
                if self.num_labels == 1:
                    loss = ops.mse_loss(logits.squeeze(), labels.squeeze())
                else:
                    loss = ops.mse_loss(logits, labels)
            elif self.config.problem_type == "single_label_classification":
                loss = ops.cross_entropy(
                    logits.view(-1, self.num_labels), labels.view(-1)
                )
            elif self.config.problem_type == "multi_label_classification":
                loss = ops.binary_cross_entropy_with_logits(logits, labels)

        if not return_dict:
            output = (logits,) + outputs[2:]
            return ((loss,) + output) if loss is not None else output

        return NatImageClassifierOutput(
            loss=loss,
            logits=logits,
            hidden_states=outputs.hidden_states,
            attentions=outputs.attentions,
            reshaped_hidden_states=outputs.reshaped_hidden_states,
        )


class NatBackbone(NatPreTrainedModel, BackboneMixin):
    def __init__(self, config):
        super().__init__(config)
        super()._init_backbone(config)

        requires_backends(self, ["mindspore"])

        self.embeddings = NatEmbeddings(config)
        self.encoder = NatEncoder(config)
        self.num_features = [config.embed_dim] + [
            int(config.embed_dim * 2**i) for i in range(len(config.depths))
        ]

        # Add layer norms to hidden states of out_features
        hidden_states_norms = {}
        for stage, num_channels in zip(self.out_features, self.channels):
            hidden_states_norms[stage] = nn.LayerNorm([num_channels])
        self.hidden_states_norms = nn.CellDict(hidden_states_norms)

        # Initialize weights and apply final processing
        self.post_init()

    def get_input_embeddings(self):
        return self.embeddings.patch_embeddings

    def construct(
        self,
        pixel_values: ms.Tensor,
        output_hidden_states: Optional[bool] = None,
        output_attentions: Optional[bool] = None,
        return_dict: Optional[bool] = None,
    ) -> BackboneOutput:
        """
        Returns:

        Examples:

        ```python
        >>> from transformers import AutoImageProcessor, AutoBackbone
        >>> import torch
        >>> from PIL import Image
        >>> import requests

        >>> url = "http://images.cocodataset.org/val2017/000000039769.jpg"
        >>> image = Image.open(requests.get(url, stream=True).raw)

        >>> processor = AutoImageProcessor.from_pretrained("shi-labs/nat-mini-in1k-224")
        >>> model = AutoBackbone.from_pretrained(
        ...     "shi-labs/nat-mini-in1k-224", out_features=["stage1", "stage2", "stage3", "stage4"]
        ... )

        >>> inputs = processor(image, return_tensors="pt")

        >>> outputs = model(**inputs)

        >>> feature_maps = outputs.feature_maps
        >>> list(feature_maps[-1].shape)
        [1, 512, 7, 7]
        ```"""
        return_dict = (
            return_dict if return_dict is not None else self.config.use_return_dict
        )
        output_hidden_states = (
            output_hidden_states
            if output_hidden_states is not None
            else self.config.output_hidden_states
        )
        output_attentions = (
            output_attentions
            if output_attentions is not None
            else self.config.output_attentions
        )

        embedding_output = self.embeddings(pixel_values)

        outputs = self.encoder(
            embedding_output,
            output_attentions=output_attentions,
            output_hidden_states=True,
            output_hidden_states_before_downsampling=True,
            return_dict=True,
        )

        hidden_states = outputs.reshaped_hidden_states

        feature_maps = ()
        for stage, hidden_state in zip(self.stage_names, hidden_states):
            if stage in self.out_features:
                # TODO can we simplify this?
                batch_size, num_channels, height, width = hidden_state.shape
                hidden_state = hidden_state.permute(0, 2, 3, 1).contiguous()
                hidden_state = hidden_state.view(
                    batch_size, height * width, num_channels
                )
                hidden_state = self.hidden_states_norms[stage](hidden_state)
                hidden_state = hidden_state.view(
                    batch_size, height, width, num_channels
                )
                hidden_state = hidden_state.permute(0, 3, 1, 2).contiguous()
                feature_maps += (hidden_state,)

        if not return_dict:
            output = (feature_maps,)
            if output_hidden_states:
                output += (outputs.hidden_states,)
            return output

        return BackboneOutput(
            feature_maps=feature_maps,
            hidden_states=outputs.hidden_states if output_hidden_states else None,
            attentions=outputs.attentions,
        )


__all__ = ["NatForImageClassification", "NatPreTrainedModel", "NatModel", "NatBackbone"]
