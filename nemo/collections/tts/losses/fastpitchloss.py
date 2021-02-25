# *****************************************************************************
#  Copyright (c) 2020, NVIDIA CORPORATION.  All rights reserved.
#
#  Redistribution and use in source and binary forms, with or without
#  modification, are permitted provided that the following conditions are met:
#      * Redistributions of source code must retain the above copyright
#        notice, this list of conditions and the following disclaimer.
#      * Redistributions in binary form must reproduce the above copyright
#        notice, this list of conditions and the following disclaimer in the
#        documentation and/or other materials provided with the distribution.
#      * Neither the name of the NVIDIA CORPORATION nor the
#        names of its contributors may be used to endorse or promote products
#        derived from this software without specific prior written permission.
#
#  THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
#  ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
#  WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
#  DISCLAIMED. IN NO EVENT SHALL NVIDIA CORPORATION BE LIABLE FOR ANY
#  DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
#  (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
#  LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
#  ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
#  (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
#  SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#
# *****************************************************************************

import torch
import torch.nn.functional as F

from nemo.collections.tts.modules.transformer import mask_from_lens
from nemo.core.classes import Loss, typecheck
from nemo.core.neural_types.elements import (
    LengthsType,
    LossType,
    MelSpectrogramType,
    RegressionValuesType,
    TokenDurationType,
    TokenLogDurationType,
)
from nemo.core.neural_types.neural_type import NeuralType


class FastPitchLoss(Loss):
    def __init__(self, dur_predictor_loss_scale=0.1, pitch_predictor_loss_scale=0.1):
        super(FastPitchLoss, self).__init__()
        self.dur_predictor_loss_scale = dur_predictor_loss_scale
        self.pitch_predictor_loss_scale = pitch_predictor_loss_scale

    @property
    def input_types(self):
        return {
            "spect_predicted": NeuralType(('B', 'D', 'T'), MelSpectrogramType()),
            "log_durs_predicted": NeuralType(('B', 'T'), TokenLogDurationType()),
            "pitch_predicted": NeuralType(('B', 'T'), RegressionValuesType()),
            "spect_tgt": NeuralType(('B', 'D', 'T'), MelSpectrogramType()),
            "durs_tgt": NeuralType(('B', 'T'), TokenDurationType()),
            "dur_lens": NeuralType(('B'), LengthsType()),
            "pitch_tgt": NeuralType(('B', 'T'), RegressionValuesType()),
        }

    @property
    def output_types(self):
        return {
            "loss": NeuralType(elements_type=LossType()),
            "mel_loss": NeuralType(elements_type=LossType()),
            "dur_loss": NeuralType(elements_type=LossType()),
            "pitch_loss": NeuralType(elements_type=LossType()),
        }

    @typecheck()
    def forward(self, spect_predicted, log_durs_predicted, pitch_predicted,
                spect_tgt, durs_tgt, dur_lens, pitch_tgt):

        spect_tgt.requires_grad = False
        spect_tgt = spect_tgt.transpose(1, 2)  # (B, T, H)

        dur_mask = mask_from_lens(dur_lens, max_len=durs_tgt.size(1))
        log_durs_tgt = torch.log(durs_tgt.float() + 1)
        loss_fn = F.mse_loss
        dur_loss = loss_fn(log_durs_predicted, log_durs_tgt, reduction='none')
        dur_loss = (dur_loss * dur_mask).sum() / dur_mask.sum()
        dur_loss *= self.dur_predictor_loss_scale

        ldiff = spect_tgt.size(1) - spect_predicted.size(1)
        spect_predicted = F.pad(spect_predicted, (0, 0, 0, ldiff, 0, 0), value=0.0)
        mel_mask = spect_tgt.ne(0).float()
        loss_fn = F.mse_loss
        mel_loss = loss_fn(spect_predicted, spect_tgt, reduction='none')
        mel_loss = (mel_loss * mel_mask).sum() / mel_mask.sum()

        ldiff = pitch_tgt.size(1) - pitch_predicted.size(1)
        pitch_predicted = F.pad(pitch_predicted, (0, ldiff, 0, 0), value=0.0)
        pitch_loss = F.mse_loss(pitch_tgt, pitch_predicted, reduction='none')
        pitch_loss = (pitch_loss * dur_mask).sum() / dur_mask.sum()
        pitch_loss *= self.pitch_predictor_loss_scale

        return mel_loss + pitch_loss + dur_loss, mel_loss, dur_loss, pitch_loss
