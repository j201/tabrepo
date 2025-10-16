from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Literal, TYPE_CHECKING
from typing_extensions import Self

from autogluon.common.utils.s3_utils import s3_path_to_bucket_prefix
import pandas as pd
import yaml

from tabrepo.loaders import Paths
from tabrepo.repository.evaluation_repository import EvaluationRepository
from tabrepo.nips2025_utils.generate_repo import generate_repo_from_results_lst
from tabrepo.benchmark.result import BaselineResult
from tabrepo.nips2025_utils.method_processor import get_info_from_result, load_raw
from tabrepo.utils.s3_utils import s3_get_object

if TYPE_CHECKING:
    from tabrepo.nips2025_utils.artifacts.method_downloader import MethodDownloaderS3
    from tabrepo.nips2025_utils.artifacts.method_uploader import MethodUploaderS3


class MethodMetadata:
    def __init__(
        self,
        method: str,
        *,
        artifact_name: str = None,
        date: str | None = None,
        method_type: Literal["config", "baseline", "portfolio"] = "config",
        name_suffix: str | None = None,
        ag_key: str | None = None,
        config_default: str | None = None,
        can_hpo: bool | None = None,
        compute: Literal["cpu", "gpu"] = "cpu",
        is_bag: bool = False,
        has_raw: bool = False,
        has_processed: bool = False,
        has_results: bool = False,
        use_artifact_name_in_prefix: bool = False,
        s3_bucket: str = None,
        s3_prefix: str = None,
        upload_as_public: bool = False,
    ):
        self.method = method
        if artifact_name is None:
            artifact_name = method
        self.artifact_name = artifact_name
        self.date = date
        self.method_type = method_type
        self.ag_key = ag_key
        self.name_suffix = name_suffix
        self.config_default = config_default
        self.compute = compute
        self.is_bag = is_bag
        self.has_raw = has_raw
        self.has_processed = has_processed
        self.has_results = has_results
        self.use_artifact_name_in_prefix = use_artifact_name_in_prefix
        if can_hpo is None:
            can_hpo = self.method_type == "config"
        self.can_hpo = can_hpo
        self.s3_bucket = s3_bucket
        self.s3_prefix = s3_prefix
        self.upload_as_public = upload_as_public

        assert isinstance(self.method, str) and len(self.method) > 0
        assert isinstance(self.artifact_name, str) and len(self.artifact_name) > 0
        assert self.method_type in ["config", "baseline", "portfolio"]
        assert self.compute in ["cpu", "gpu"]

    @property
    def config_type(self) -> str | None:
        if self.method_type != "config":
            return None
        elif self.name_suffix is not None:
            return f"{self.ag_key}{self.name_suffix}"
        else:
            return self.ag_key

    # TODO: Also support baseline methods
    @classmethod
    def from_raw(
        cls,
        results_lst: list[BaselineResult],
        method: str | None = None,
        artifact_name: str | None = None,
        config_default: str | None = None,
        compute: Literal["cpu", "gpu"] | None = None,
    ) -> Self:
        result_lst_dict = []

        for r in results_lst:
            cur_result = get_info_from_result(result=r)
            result_lst_dict.append(cur_result)
        result_df = pd.DataFrame(result_lst_dict)

        unique_method_types = result_df["method_type"].unique()
        assert len(unique_method_types) == 1
        method_type = unique_method_types[0]

        if method_type == "config":
            method_metadata = cls._from_raw_config(
                result_df=result_df,
                method=method,
                artifact_name=artifact_name,
                config_default=config_default,
                compute=compute,
            )
        elif method_type == "baseline":
            method_metadata = cls._from_raw_baseline(
                result_df=result_df,
                method=method,
                artifact_name=artifact_name,
                compute=compute,
            )
        else:
            raise ValueError(f"Unknown method_type: {method_type}")

        return method_metadata

    @classmethod
    def _from_raw_baseline(
        cls,
        result_df: pd.DataFrame,
        method: str | None = None,
        artifact_name: str | None = None,
        compute: Literal["cpu", "gpu"] | None = None,
    ) -> Self:
        unique_method_types = result_df["method_type"].unique()
        assert len(unique_method_types) == 1
        method_type = unique_method_types[0]

        assert method_type == "baseline"

        unique_methods = result_df["framework"].unique()
        assert len(unique_methods) == 1
        if method is None:
            method = unique_methods[0]

        unique_num_gpus = result_df["num_gpus"].unique()
        assert len(unique_num_gpus) == 1
        num_gpus = unique_num_gpus[0]

        if compute is None:
            compute: Literal["cpu", "gpu"] = "cpu" if num_gpus == 0 else "gpu"

        if artifact_name is None:
            artifact_name = method

        _method_metadata = cls(
            method=method,
            artifact_name=artifact_name,
            method_type=method_type,
            compute=compute,
            config_default=None,
            can_hpo=False,
            is_bag=False,
            has_raw=True,
            has_processed=True,
            has_results=True,
        )

        return _method_metadata

    @classmethod
    def _from_raw_config(
        cls,
        result_df: pd.DataFrame,
        method: str | None = None,
        artifact_name: str | None = None,
        config_default: str | None = None,
        compute: Literal["cpu", "gpu"] | None = None,
    ) -> Self:
        unique_method_types = result_df["method_type"].unique()
        assert len(unique_method_types) == 1
        method_type = unique_method_types[0]

        assert method_type == "config"

        unique_model_types = result_df["model_type"].unique()
        assert len(unique_model_types) == 1, f"MethodMetadata requires exactly 1 model type, found: {unique_model_types}"

        unique_num_gpus = result_df["num_gpus"].unique()
        assert len(unique_num_gpus) == 1
        num_gpus = unique_num_gpus[0]

        if compute is None:
            compute: Literal["cpu", "gpu"] = "cpu" if num_gpus == 0 else "gpu"

        unique_ag_key = result_df["ag_key"].unique()
        assert len(unique_ag_key) == 1
        ag_key = unique_ag_key[0]

        is_bag = bool(result_df["is_bag"].any())

        unique_name_prefix = result_df["name_prefix"].unique()
        assert len(unique_name_prefix) == 1
        name_prefix = unique_name_prefix[0]

        unique_methods = result_df["framework"].unique()
        if len(unique_methods) == 1:
            _config_default = unique_methods[0]
            can_hpo = False
        else:
            _config_default = None
            can_hpo = True
        if config_default is None:
            config_default = _config_default

        if method is None:
            method = name_prefix

        if artifact_name is None:
            artifact_name = method

        _method_metadata = cls(
            method=method,
            artifact_name=artifact_name,
            method_type=method_type,
            compute=compute,
            config_default=config_default,
            ag_key=ag_key,
            can_hpo=can_hpo,
            is_bag=is_bag,
            has_raw=True,
            has_processed=True,
            has_results=True,
        )

        return _method_metadata

    @property
    def has_s3_cache(self) -> bool:
        return self.s3_bucket is not None and self.s3_prefix is not None

    @property
    def has_configs_hyperparameters(self) -> bool:
        return self.method_type == "config"

    @property
    def _path_root(self) -> Path:
        return Paths.artifacts_root_cache_tabarena

    @property
    def path_cache_root(self) -> Path:
        return Paths._tabarena_root_cache

    @property
    def path(self) -> Path:
        return self._path_root / self.artifact_name / "methods" / self.method

    @property
    def path_raw(self) -> Path:
        return self.path / "raw"

    @property
    def path_processed(self) -> Path:
        return self.path / "processed"

    @property
    def path_processed_holdout(self) -> Path:
        return self.path / "processed_holdout"

    @property
    def path_results(self) -> Path:
        return self.path / "results"

    @property
    def path_results_holdout(self) -> Path:
        return self.path_results / "holdout"

    @property
    def path_raw_exists(self) -> bool:
        return self.path_raw.is_dir()

    @property
    def path_processed_exists(self) -> bool:
        return self.path_processed.is_dir()

    @property
    def path_results_exists(self) -> bool:
        return self.path_results.is_dir()

    def path_results_hpo(self, holdout: bool = False) -> Path:
        path_prefix = self.path_results_holdout if holdout else self.path_results
        return path_prefix / "hpo_results.parquet"

    def path_results_model(self, holdout: bool = False) -> Path:
        path_prefix = self.path_results_holdout if holdout else self.path_results
        return path_prefix / "model_results.parquet"

    def path_results_portfolio(self, holdout: bool = False) -> Path:
        path_prefix = self.path_results_holdout if holdout else self.path_results
        return path_prefix / "portfolio_results.parquet"

    def relative_to_cache_root(self, path: Path) -> Path:
        return path.relative_to(self.path_cache_root)

    def relative_to_root(self, path: Path) -> Path:
        return path.relative_to(self._path_root)

    def relative_to_method(self, path: Path) -> Path:
        return path.relative_to(self.path)

    def to_s3_cache_loc(self, path: Path, s3_cache_root: str) -> str:
        path_suffix: str = self.relative_to_cache_root(path=path).as_posix()
        s3_cache_path = f"{s3_cache_root}/{path_suffix}"
        return s3_cache_path

    def method_downloader(self, verbose: bool = False) -> MethodDownloaderS3:
        if not self.has_s3_cache:
            raise AssertionError(
                f"Tried to get MethodDownloaderS3 from MethodMetadata, "
                f"but s3_bucket and/or s3_prefix were not specified!"
                f"\n\t(method={self.method}, artifact_name={self.artifact_name}, "
                f"s3_bucket={self.s3_bucket}, s3_prefix={self.s3_prefix})"
                f"\nEnsure you initialize MethodMetadata with s3_bucket and s3_prefix to enable s3 artifact download."
            )
        from tabrepo.nips2025_utils.artifacts.method_downloader import MethodDownloaderS3
        return MethodDownloaderS3(
            method_metadata=self,
            s3_bucket=self.s3_bucket,
            s3_prefix=self.s3_prefix,
            verbose=verbose,
            clear_dirs=False,
        )

    def method_uploader(self) -> MethodUploaderS3:
        if not self.has_s3_cache:
            raise AssertionError(
                f"Tried to get MethodUploaderS3 from MethodMetadata, "
                f"but s3_bucket and/or s3_prefix were not specified!"
                f"\n\t(method={self.method}, artifact_name={self.artifact_name}, "
                f"s3_bucket={self.s3_bucket}, s3_prefix={self.s3_prefix})"
                f"\nEnsure you initialize MethodMetadata with s3_bucket and s3_prefix to enable s3 artifact upload."
            )
        from tabrepo.nips2025_utils.artifacts.method_uploader import MethodUploaderS3
        return MethodUploaderS3(
            method_metadata=self,
            s3_bucket=self.s3_bucket,
            s3_prefix=self.s3_prefix,
            upload_as_public=self.upload_as_public,
        )

    def load_model_results(self, holdout: bool = False) -> pd.DataFrame:
        return pd.read_parquet(path=self.path_results_model(holdout=holdout))

    def load_hpo_results(self, holdout: bool = False) -> pd.DataFrame:
        return pd.read_parquet(path=self.path_results_hpo(holdout=holdout))

    def load_portfolio_results(self, holdout: bool = False) -> pd.DataFrame:
        return pd.read_parquet(path=self.path_results_portfolio(holdout=holdout))

    def load_paper_results(self, holdout: bool = False) -> pd.DataFrame:
        if self.method_type == "config":
            df_results = self.load_hpo_results(holdout=holdout)
        elif self.method_type == "baseline":
            df_results = self.load_model_results(holdout=holdout)
        elif self.method_type == "portfolio":
            df_results = self.load_portfolio_results(holdout=holdout)
        else:
            raise ValueError(f"Unknown method_type: {self.method_type} for method {self.method}")
        return df_results

    def path_configs_hyperparameters(self, holdout: bool = False) -> Path:
        if holdout:
            path_processed = self.path_processed_holdout
        else:
            path_processed = self.path_processed
        path_configs_hyperparameters = path_processed / "configs_hyperparameters.json"
        return path_configs_hyperparameters

    def load_configs_hyperparameters(self, holdout: bool = False, download: str | bool = "auto") -> dict[str, dict]:
        if download == "auto":
            try:
                return self.load_configs_hyperparameters(holdout=holdout, download=False)
            except FileNotFoundError as err:
                print(
                    f"Cache miss detected for configs_hyperparameters.json "
                    f"(method={self.method}), attempting download..."
                )
                out = self.load_configs_hyperparameters(holdout=holdout, download=True)
                print(f"\tDownload successful")
                return out
        elif isinstance(download, bool) and download:
            self.download_configs_hyperparameters(holdout=holdout)
        with open(self.path_configs_hyperparameters(holdout=holdout), "r") as f:
            out = json.load(f)
        return out

    def download_configs_hyperparameters(self, holdout: bool = False):
        method_downloader = self.method_downloader()
        method_downloader.download_configs_hyperparameters(holdout=holdout)

    def load_raw(
        self,
        path_raw: str | Path = None,
        engine: str = "ray",
        as_holdout: bool = False,
    ) -> list[BaselineResult]:
        """
        Loads the raw results artifacts from all `results.pkl` files in the `path_raw` directory

        Parameters
        ----------
        path_raw
        engine
        as_holdout

        Returns
        -------

        """
        if path_raw is None:
            path_raw = self.path_raw
        return load_raw(path_raw=path_raw, engine=engine, as_holdout=as_holdout)

    def load_processed(
        self,
        path_processed: str | Path = None,
        prediction_format: Literal["memmap", "memopt", "mem"] = "memmap",
        as_holdout: bool = False,
        verbose: bool = False,
    ) -> EvaluationRepository:
        if path_processed is None:
            if as_holdout:
                path_processed = self.path_processed_holdout
            else:
                path_processed = self.path_processed
        repo = EvaluationRepository.from_dir(
            path=path_processed,
            prediction_format=prediction_format,
            verbose=verbose,
        )
        return repo

    def generate_repo(
        self,
        results_lst: list[BaselineResult] = None,
        task_metadata: pd.DataFrame = None,
        cache: bool = False,
        engine: str = "ray",
    ) -> EvaluationRepository:
        if results_lst is None:
            results_lst = self.load_raw(engine=engine)

        repo: EvaluationRepository = generate_repo_from_results_lst(
            results_lst=results_lst,
            task_metadata=task_metadata,
            name_suffix=self.name_suffix,
        )

        if cache:
            repo.to_dir(self.path_processed)
        return repo

    @property
    def path_metadata(self) -> Path:
        return self.path / "metadata.yaml"

    def to_yaml(self, path: Path | str = None):
        if path is None:
            path = self.path_metadata
        assert str(path).endswith(".yaml")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w') as outfile:
            yaml.dump(self.__dict__, outfile, default_flow_style=False)

    def to_yaml_fileobj(self) -> io.BytesIO:
        """
        Serialize this object to YAML and return a BytesIO buffer suitable for
        s3_client.upload_fileobj, without writing to local disk.

        Returns
        -------
        io.BytesIO
            Buffer positioned at start containing UTF-8 encoded YAML.
        """
        yaml_str = yaml.safe_dump(
            self.__dict__,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        )
        buf = io.BytesIO(yaml_str.encode("utf-8"))
        buf.seek(0)
        return buf

    @classmethod
    def from_yaml(
        cls,
        path: Path | str = None,
        method: str = None,
        artifact_name: str = None,
    ) -> Self:
        if path is None:
            assert method is not None, f"method must be specified if path is not specified"
            assert artifact_name is not None, f"artifact_name must be specified if path is not specified"
            path = Paths._tabarena_root_cache / "artifacts" / artifact_name / "methods" / method / "metadata.yaml"

        assert str(path).endswith(".yaml")
        with open(path, 'r') as file:
            kwargs = yaml.safe_load(file)
        return cls(**kwargs)

    @classmethod
    def from_s3_cache(
        cls,
        method: str,
        s3_bucket: str,
        s3_prefix: str = "cache",
        artifact_name: str = None,
    ) -> Self:
        metadata = MethodMetadata(
            method=method,
            artifact_name=artifact_name,
        )
        path_local = Path(metadata.path_metadata)
        s3_cache_root = f"s3://{s3_bucket}/{s3_prefix}"
        s3_path_loc = metadata.to_s3_cache_loc(path=Path(path_local), s3_cache_root=s3_cache_root)
        _, s3_key = s3_path_to_bucket_prefix(s3_path_loc)
        # Stream into memory
        try:
            obj = s3_get_object(Bucket=s3_bucket, Key=s3_key)
        except Exception as e:
            print(
                f"Failed to fetch MethodMetadata yaml file from s3! Maybe it doesn't exist or is not public?"
                f'\n\t(method="{method}", artifact_name="{artifact_name}", '
                f's3_bucket="{s3_bucket}", s3_prefix="{s3_prefix}")'
            )
            raise e

        body = obj["Body"]  # file-like object (StreamingBody, BytesIO, etc.)
        kwargs = yaml.safe_load(body)

        if "s3_bucket" not in kwargs:
            # yaml created before s3_bucket existed
            kwargs["s3_bucket"] = s3_bucket
        if "s3_prefix" not in kwargs:
            # yaml created before s3_prefix existed
            kwargs["s3_prefix"] = s3_prefix

        return cls(**kwargs)

    def cache_raw(
        self,
        results_lst: list[BaselineResult],
    ):
        path = self.path_raw
        for result in results_lst:
            result.to_dir(path=path)

    def cache_processed(self, repo: EvaluationRepository):
        repo.to_dir(self.path_processed)

    def path_results_files(self, holdout: bool = False) -> list[Path]:
        if self.method_type == "portfolio":
            file_names = [
                self.path_results_portfolio(holdout=holdout)
            ]
        else:
            file_names = [
                self.path_results_model(holdout=holdout)
            ]

        if self.method_type == "config":
            file_names.append(self.path_results_hpo(holdout=holdout))
        return file_names
