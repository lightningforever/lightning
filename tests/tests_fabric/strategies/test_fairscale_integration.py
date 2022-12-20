# Copyright The PyTorch Lightning team.
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
import os

import pytest
import torch
from tests_fabric.helpers.models import BoringLite
from tests_fabric.helpers.runif import RunIf


class ShardedSaveAndLoad(BoringLite):
    def get_optimizer(self, module):
        optimizer = super().get_optimizer(module)
        if self.with_fairscale_oss:
            from fairscale.optim import OSS

            optimizer = OSS(params=optimizer.param_groups, optim=type(optimizer), **optimizer.defaults)
        return optimizer

    def run(self, tmpdir, with_fairscale_oss=False):
        self.with_fairscale_oss = with_fairscale_oss

        super().run()

        from fairscale.nn import ShardedDataParallel
        from fairscale.optim import OSS

        # the model and optimizer is wrapped correctly
        assert isinstance(self.model._forward_module, ShardedDataParallel)
        assert isinstance(self.optimizer.optimizer, OSS)

        self.model.cpu()

        checkpoint_path = os.path.join(tmpdir, "checkpoint.ckpt")
        # need to broadcast because tmpdir is different on each process
        checkpoint_path = self.broadcast(checkpoint_path)

        checkpoint = {"model": self.model.state_dict(), "optimizer": self.optimizer.state_dict()}
        self.save(checkpoint, checkpoint_path)

        self.barrier()  # ensure the checkpoint is saved before load

        loaded_checkpoint = self.load(checkpoint_path)
        new_model = self.get_model()
        new_model.load_state_dict(loaded_checkpoint["model"])

        # Assert model parameters are identical after loading
        for trained_param, loaded_param in zip(self.model.parameters(), new_model.parameters()):
            assert torch.equal(trained_param, loaded_param)


@RunIf(fairscale=True)
@pytest.mark.parametrize("accelerator", ["cpu", pytest.param("cuda", marks=RunIf(min_cuda_gpus=2))])
@pytest.mark.parametrize("strategy", (pytest.param("ddp_sharded", marks=RunIf(standalone=True)), "ddp_sharded_spawn"))
@pytest.mark.parametrize("with_fairscale_oss", (True, False))
def test_fairscale_multi_process_checkpoint_state_consolidation(with_fairscale_oss, strategy, accelerator, tmpdir):
    """Test that the sharded optimizer states get consolidated when saving the checkpoint, and that the loaded
    weights is identical to the saved one."""
    lite = ShardedSaveAndLoad(strategy=strategy, accelerator=accelerator, devices=2)
    lite.run(tmpdir, with_fairscale_oss=with_fairscale_oss)
