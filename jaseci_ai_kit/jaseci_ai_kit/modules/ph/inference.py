from http.client import ImproperConnectionState
from typing import Any, Dict
import torch
import uuid as uuid_gen
import os
import shutil

from .utils import model as model_module
from .utils import process as process_module
from .utils.logger import get_logger
import logging
from .utils.util import deep_update, write_yaml

from .train import train

import warnings

warnings.filterwarnings("ignore")


class InferenceEngine:
    """
    Inference Engine for each user.
    @param: config: Dict
    """

    def __init__(self, config: Dict, uuid: str = None):
        self.ph_config = config
        self.id = uuid if uuid else str(uuid_gen.uuid4())

        os.makedirs(f"heads/{self.id}", exist_ok=True)
        write_yaml(self.ph_config, f"heads/{self.id}/config.yaml")

        self.logger = get_logger(name=self.id)
        self.logger.addHandler(logging.FileHandler(f"heads/{self.id}/log.txt"))
        self.logger.setLevel(logging.DEBUG)

        self.infer_config = config["Inference"]

        # Building the Model
        model_config = config["Model"]
        self.model = getattr(model_module, model_config["type"])(
            **model_config.get("args", {})
        )

        # Loading the weights
        if self.infer_config["weights"]:
            shutil.copyfile(
                self.infer_config["weights"], f"heads/{self.id}/current.pth"
            )
            self.logger.info(
                "Loading default checkpoint: {} ...".format(
                    self.infer_config["weights"]
                )
            )
            checkpoint = torch.load(f"heads/{self.id}/current.pth")
            state_dict = checkpoint["state_dict"]
            self.model.load_state_dict(state_dict)

        # Setting the device
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.model = self.model.to(self.device)
        self.model.eval()

        # Initialize Pre-processor
        self.preprocessor = getattr(
            process_module, self.infer_config["preprocess"]["type"]
        )(**self.infer_config["preprocess"].get("args", {}))
        # Initialize Post-processor
        self.postprocessor = getattr(
            process_module, self.infer_config["postprocess"]["type"]
        )(**self.infer_config["postprocess"].get("args", {}))

    @torch.no_grad()
    def predict(self, data: Any) -> Any:
        data = self.preprocessor.process(data)
        data = data.to(self.device)
        output = self.model(data)
        self.logger.info("Predict is called")
        return self.postprocessor.process(output)

    def load_weights(self, weights: str) -> None:
        self.logger.info("Loading new weights: {} ...".format(weights))
        checkpoint = torch.load(weights)
        state_dict = checkpoint["state_dict"]
        self.model.load_state_dict(state_dict)

    def __del__(self):
        del self.model
        del self.device
        torch.cuda.empty_cache()


class InferenceList:
    """
    Parent of Inference Engines. use to manage multiple Inference Engines.
    """

    def __init__(self, config: Dict = None) -> None:
        self.config = config
        os.makedirs("heads", exist_ok=True)
        self.ie_list = {}

    def add(self, config: Dict = None, uuid: str = None) -> None:
        if self.check(uuid):
            raise ImproperConnectionState(
                f"{self.check(uuid)} Inference Engine already exists. Please use another uuid."
            )
        if config:
            ie = InferenceEngine(config, uuid)
        else:
            ie = InferenceEngine(self.config, uuid)
        self.ie_list[ie.id] = ie
        return ie.id

    def predict(self, uuid: str, data: Any) -> Any:
        if self.check(uuid):
            return self.ie_list[uuid].predict(data)
        else:
            raise ImproperConnectionState("Inference Engine not found.")

    def train(self, uuid: str, config: Dict = None, auto_update=True) -> None:
        if self.check(uuid):
            ph_config = self.ie_list[uuid].ph_config
            if config:
                deep_update(ph_config, config)
                write_yaml(ph_config, f"heads/{uuid}/config.yaml")
            resume = (
                f"heads/{uuid}/current.pth"
                if os.path.exists(f"heads/{uuid}/current.pth")
                else None
            )

            run_id = train(
                {
                    "uuid": uuid,
                    "config": f"heads/{uuid}/config.yaml",
                    "resume": resume,
                    "device": None,
                }
            )
            if auto_update:
                self.update_head(uuid, run_id)
        else:
            raise ImproperConnectionState("Inference Engine not found.")

    def update_head(self, uuid: str, run_id: str) -> None:
        shutil.copyfile(
            f"heads/{uuid}/runs/{run_id}/model_best.pth", f"heads/{uuid}/current.pth"
        )
        self.ie_list[uuid].load_weights(f"heads/{uuid}/current.pth")

    def get_config(self, uuid: str) -> Dict:
        if self.check(uuid):
            return self.ie_list[uuid].ph_config
        else:
            raise ImproperConnectionState("Inference Engine not found.")

    def check(self, uuid: str) -> bool:
        return uuid in self.ie_list
