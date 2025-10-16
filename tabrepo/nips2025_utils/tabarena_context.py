from __future__ import annotations

import copy
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd

from autogluon.common.savers import save_pd

from tabrepo.benchmark.result import BaselineResult
from tabrepo.utils.pickle_utils import fetch_all_pickles
from tabrepo.nips2025_utils.fetch_metadata import load_task_metadata
from tabrepo import EvaluationRepository, EvaluationRepositoryCollection
from tabrepo.repository.abstract_repository import AbstractRepository
from tabrepo.nips2025_utils.generate_repo import generate_repo_from_paths
from tabrepo.paper.paper_runner_tabarena import PaperRunTabArena
from tabrepo.nips2025_utils.artifacts import tabarena_method_metadata_collection
from tabrepo.nips2025_utils.artifacts.method_metadata import MethodMetadata
from tabrepo.nips2025_utils.artifacts.method_metadata_collection import MethodMetadataCollection
from tabrepo.nips2025_utils.eval_all import evaluate_all


_methods_paper = [
    "AutoGluon_v130",
    # "Portfolio-N200-4h",

    "CatBoost",
    # "Dummy",
    "ExplainableBM",
    "ExtraTrees",
    "KNeighbors",
    "LightGBM",
    "LinearModel",
    # "ModernNCA",
    "NeuralNetFastAI",
    "NeuralNetTorch",
    "RandomForest",
    # "RealMLP",
    # "TabM",
    "XGBoost",

    # "Mitra_GPU",
    "ModernNCA_GPU",
    "RealMLP_GPU",
    "TabDPT_GPU",
    "TabICL_GPU",
    "TabM_GPU",
    "TabPFNv2_GPU",

    "xRFM_GPU",
    # "LimiX_GPU",
    # "BetaTabPFN_GPU",
    # "TabFlex_GPU",
]


class TabArenaContext:
    def __init__(
        self,
        methods: list[MethodMetadata] | str = "tabarena",
        extra_methods: list[MethodMetadata] = None,
        include_ag_140: bool = True,
        include_mitra: bool = True,
        include_unverified: bool = False,
        backend: Literal["ray", "native"] = "ray",
    ):
        self.task_metadata = load_task_metadata(paper=True)  # FIXME: Instead download?
        assert backend in ["ray", "native"]
        self.backend = backend
        self.engine = "ray" if self.backend == "ray" else "sequential"
        if isinstance(methods, str):
            if methods != "tabarena":
                raise ValueError(f"Unknown methods preset '{methods}'.")
            methods = copy.deepcopy(_methods_paper)
            if include_ag_140:
                methods.append("AutoGluon_v140")
            if include_mitra:
                methods.append("Mitra_GPU")
            if include_unverified:
                methods.extend([
                    "LimiX_GPU",
                    "BetaTabPFN_GPU",
                    "TabFlex_GPU",
                ])
            method_metadata_lst: list[MethodMetadata] = copy.deepcopy(
                tabarena_method_metadata_collection.method_metadata_lst
            )
        else:
            method_metadata_lst = methods
            methods = [m.method for m in method_metadata_lst]
        method_metadata_name_set = {m.method for m in method_metadata_lst}
        for m in methods:
            assert m in method_metadata_name_set, f"Missing method {m!r} in method_metadata_lst!"
        method_metadata_lst = [m for m in method_metadata_lst if m.method in set(methods)]

        if extra_methods:
            for method_metadata in extra_methods:
                assert method_metadata.method not in methods
                methods.append(method_metadata.method)
                method_metadata_lst.append(method_metadata)

        self.method_metadata_collection: MethodMetadataCollection = MethodMetadataCollection(method_metadata_lst)

    def compare(
        self,
        output_dir: str | Path,
        new_results: pd.DataFrame | None = None,
        only_valid_tasks: bool = False,
        subset: str | None = None,
        folds: list[int] | None = None,
        score_on_val: bool = False,
        average_seeds: bool = True,
        tmp_treat_tasks_independently: bool = False,
        leaderboard_kwargs: dict | None = None,
    ) -> pd.DataFrame:
        from tabrepo.nips2025_utils.compare import compare_on_tabarena
        return compare_on_tabarena(
            output_dir=output_dir,
            new_results=new_results,
            only_valid_tasks=only_valid_tasks,
            subset=subset,
            folds=folds,
            tabarena_context=self,
            score_on_val=score_on_val,
            average_seeds=average_seeds,
            tmp_treat_tasks_independently=tmp_treat_tasks_independently,
            leaderboard_kwargs=leaderboard_kwargs,
        )

    @property
    def methods(self) -> list[str]:
        return [m.method for m in self.method_metadata_collection.method_metadata_lst]

    def method_metadata(
        self,
        method: str,
        artifact_name: str | None = None,
        s3_bucket: str | None = None,
        s3_prefix: str | None = None,
    ) -> MethodMetadata:
        return self.method_metadata_collection.get_method_metadata(
            method=method,
            artifact_name=artifact_name,
            s3_bucket=s3_bucket,
            s3_prefix=s3_prefix,
        )

    def load_raw(self, method: str, as_holdout: bool = False) -> list[BaselineResult]:
        metadata: MethodMetadata = self.method_metadata(method=method)
        results_lst = metadata.load_raw(engine=self.engine, as_holdout=as_holdout)
        return results_lst

    def load_repo(self, methods: list[str | MethodMetadata] | None = None, config_fallback: str | None = None) -> EvaluationRepositoryCollection:
        if methods is None:
            methods = self.methods
        repos = []
        for method in methods:
            if isinstance(method, MethodMetadata):
                metadata = method
            else:
                metadata = self.method_metadata(method=method)
            cur_repo = metadata.load_processed()
            repos.append(cur_repo)
        repo = EvaluationRepositoryCollection(repos=repos, config_fallback=config_fallback)
        return repo

    def generate_repo(self, method: str) -> Path:
        metadata = self.method_metadata(method=method)

        path_raw = metadata.path_raw
        path_processed = metadata.path_processed

        name_suffix = metadata.name_suffix

        file_paths_method = fetch_all_pickles(dir_path=path_raw, suffix="results.pkl")
        repo: EvaluationRepository = generate_repo_from_paths(
            result_paths=file_paths_method,
            task_metadata=self.task_metadata,
            engine=self.engine,
            name_suffix=name_suffix,
        )

        repo.to_dir(path_processed)
        return path_processed

    def generate_repo_holdout(self, method: str) -> Path:
        metadata = self.method_metadata(method=method)

        path_raw = metadata.path_raw
        path_processed = metadata.path_processed_holdout

        name_suffix = metadata.name_suffix

        file_paths_method = fetch_all_pickles(dir_path=path_raw, suffix="results.pkl")
        repo: EvaluationRepository = generate_repo_from_paths(
            result_paths=file_paths_method,
            task_metadata=self.task_metadata,
            engine=self.engine,
            name_suffix=name_suffix,
            as_holdout=True,
        )

        repo.to_dir(path_processed)
        return path_processed

    def run_hpo(
        self,
        method: str,
        repo: EvaluationRepository,
        n_iterations: int = 40,
        n_configs: int | None = None,
        time_limit: float | None = None,
        fit_order: Literal["original", "random"] = "original",
        seed: int = 0,
    ) -> pd.DataFrame:
        simulator = PaperRunTabArena(repo=repo, backend=self.backend)
        df_results_family_hpo = simulator.run_ensemble_config_type(
            config_type=method,
            n_iterations=n_iterations,
            n_configs=n_configs,
            time_limit=time_limit,
            fit_order=fit_order,
            seed=seed,
        )
        df_results_family_hpo = df_results_family_hpo.rename(columns={
            "framework": "method",
        })
        df_results_family_hpo["method"] = f"HPO-N{n_configs}-{method}"
        return df_results_family_hpo

    def simulate_repo(
        self,
        method: str | MethodMetadata,
        repo: EvaluationRepository | None = None,
        holdout: bool = False,
        use_rf_config_fallback: bool = True,
        cache: bool = True,
    ) -> tuple[pd.DataFrame | None, pd.DataFrame]:
        if isinstance(method, MethodMetadata):
            metadata = method
            method = metadata.method
        else:
            metadata = self.method_metadata(method=method)

        save_file = str(metadata.path_results_hpo(holdout=holdout))
        save_file_model = str(metadata.path_results_model(holdout=holdout))
        if repo is None:
            if holdout:
                path_processed = metadata.path_processed_holdout
            else:
                path_processed = metadata.path_processed
            repo = EvaluationRepository.from_dir(path=path_processed)

        if metadata.method_type == "config":
            model_types = repo.config_types()
            assert len(model_types) == 1
            model_type = model_types[0]
        else:
            model_type = None

        if use_rf_config_fallback:
            metadata_rf = self.method_metadata(method="RandomForest")
            if holdout:
                config_fallback = "RandomForest_r1_BAG_L1_HOLDOUT"  # FIXME: Avoid hardcoding
                path_processed_rf = metadata_rf.path_processed_holdout
            else:
                config_fallback = metadata_rf.config_default
                path_processed_rf = metadata_rf.path_processed

            # FIXME: Try to avoid this being expensive
            if config_fallback not in repo.configs():
                repo_rf = EvaluationRepository.from_dir(path=path_processed_rf)
                repo_rf_mini = repo_rf.subset(configs=[config_fallback])
                repo = EvaluationRepositoryCollection(repos=[repo, repo_rf_mini])
            repo.set_config_fallback(config_fallback=config_fallback)
        simulator = PaperRunTabArena(repo=repo, backend=self.backend)
        # FIXME: do this in simulator automatically

        if metadata.method_type == "config":
            hpo_results = simulator.run_minimal_single(model_type=model_type, tune=metadata.can_hpo)
            hpo_results["ta_name"] = metadata.method
            hpo_results["ta_suite"] = metadata.artifact_name
            hpo_results = hpo_results.rename(columns={"framework": "method"})  # FIXME: Don't do this, make it method by default
            if cache:
                save_pd.save(path=save_file, df=hpo_results)
            config_results = simulator.run_config_family(config_type=model_type)
            baseline_results = None
        else:
            hpo_results = None
            config_results = None
            baseline_results = simulator.run_baselines()

        results_lst = [config_results, baseline_results]
        results_lst = [r for r in results_lst if r is not None]
        model_results = pd.concat(results_lst, ignore_index=True)

        model_results["ta_name"] = metadata.method
        model_results["ta_suite"] = metadata.artifact_name
        model_results = model_results.rename(columns={"framework": "method"})  # FIXME: Don't do this, make it method by default
        if cache:
            save_pd.save(path=save_file_model, df=model_results)

        return hpo_results, model_results

    def simulate_portfolio_from_configs(
        self,
        configs: list[str],
        config_fallback: str | None = None,
        repo: EvaluationRepositoryCollection = None,
    ):
        if repo is None:
            repo = self.load_repo(config_fallback=config_fallback)
        simulator = PaperRunTabArena(repo=repo, backend=self.backend)

        results = simulator.evaluate_ensembles(
            configs=configs,
        )

        results = results.rename(columns={"framework": "method"})
        return results

    def simulate_portfolio(self, methods: list[str], config_fallback: str, repo: EvaluationRepositoryCollection = None):
        if repo is None:
            repo = self.load_repo(methods=methods, config_fallback=config_fallback)
        simulator = PaperRunTabArena(repo=repo, backend=self.backend)

        df_results_n_portfolio = []
        n_portfolios = [200]
        for n_portfolio in n_portfolios:
            df_results_n_portfolio.append(
                simulator.run_zs(n_portfolios=n_portfolio, n_ensemble=None, n_ensemble_in_name=False))
        results = pd.concat(df_results_n_portfolio, ignore_index=True)

        results = results.rename(columns={"framework": "method"})
        return results

    def run_portfolio_from_config_types(
        self,
        repo: AbstractRepository,
        config_types: list[str],
        n_portfolio: int,
        n_ensemble: int | None = None,
        time_limit: int | None = None,
    ) -> pd.DataFrame:
        simulator = PaperRunTabArena(repo=repo, backend=self.backend)
        cur_result = simulator.run_zs_from_types(
            config_types=config_types,
            n_portfolios=n_portfolio,
            n_ensemble=n_ensemble,
            n_ensemble_in_name=True,
            time_limit=time_limit,
        )
        return cur_result

    def load_hpo_results(self, method: str, holdout: bool = False) -> pd.DataFrame:
        metadata = self.method_metadata(method=method)
        return metadata.load_hpo_results(holdout=holdout)

    def load_config_results(self, method: str, holdout: bool = False) -> pd.DataFrame:
        metadata = self.method_metadata(method=method)
        return metadata.load_model_results(holdout=holdout)

    def load_portfolio_results(self, method: str, holdout: bool = False) -> pd.DataFrame:
        metadata = self.method_metadata(method=method)
        return metadata.load_portfolio_results(holdout=holdout)

    def load_results_paper(
        self,
        methods: list[str] | None = None,
        holdout: bool = False,
        download_results: str | bool = "auto",
        methods_drop: list[str] | None = None,
    ) -> pd.DataFrame:
        if methods is None:
            methods = self.methods
            if holdout:
                # only include methods that can have holdout
                methods = [m for m in methods if self.method_metadata(m).is_bag]
        if methods_drop is not None:
            for method in methods_drop:
                if method not in methods:
                    raise AssertionError(
                        f"Specified '{method}' in `methods_drop`, "
                        f"but '{method}' is not present in methods: {methods}"
                    )
            methods = [method for method in methods if method not in methods_drop]

        df_results_lst = []
        for method in methods:
            method_metadata = self.method_metadata(method=method)
            if isinstance(download_results, bool) and download_results:
                method_downloader = method_metadata.method_downloader()
                method_downloader.download_results(holdout=holdout)

            try:
                df_results = method_metadata.load_paper_results(holdout=holdout)
            except FileNotFoundError as err:
                if isinstance(download_results, str) and download_results == "auto":
                    print(
                        f"Missing local results files for method! "
                        f"Attempting to download from s3 and retry... "
                        f'(download_results={download_results}, method="{method_metadata.method}")'
                    )
                    method_downloader = method_metadata.method_downloader()
                    method_downloader.download_results(holdout=holdout)
                    df_results = method_metadata.load_paper_results(holdout=holdout)
                else:
                    print(
                        f"Missing local results files for method {method_metadata.method}! "
                        f"Try setting `download_results=True` to get the required files."
                    )
                    raise err
            df_results_lst.append(df_results)

        df_results = pd.concat(df_results_lst, ignore_index=True)
        return df_results

    def load_configs_hyperparameters(
        self,
        methods: list[str] | None = None,
        holdout: bool = False,
        download: bool | str = False,
    ) -> dict[str, dict]:
        if methods is None:
            methods = self.methods
            methods = [m for m in methods if self.method_metadata(m).method_type == "config"]
            if holdout:
                # only include methods that can have holdout
                methods = [m for m in methods if self.method_metadata(m).is_bag]
        configs_hyperparameters_lst = []
        for method in methods:
            metadata = self.method_metadata(method=method)
            configs_hyperparameters = metadata.load_configs_hyperparameters(holdout=holdout, download=download)
            configs_hyperparameters_lst.append(configs_hyperparameters)

        def merge_dicts_no_duplicates(dicts: list[dict]) -> dict:
            merged = {}
            for d in dicts:
                for key in d:
                    if key in merged:
                        raise KeyError(
                            f"Duplicate key found in configs_hyperparameters: {key}\n"
                            f"This should never happen and may mean that a given config name "
                            f"belongs to multiple different hyperparameters!"
                        )
                merged.update(d)
            return merged

        configs_hyperparameters = merge_dicts_no_duplicates(configs_hyperparameters_lst)
        return configs_hyperparameters

    def evaluate_all(
        self,
        df_results: pd.DataFrame,
        save_path: str | Path,
        df_results_holdout: pd.DataFrame = None,
        df_results_cpu: pd.DataFrame = None,
        configs_hyperparameters: dict[str, dict] = None,
        elo_bootstrap_rounds: int = 100,
        use_latex: bool = False,
        realmlp_cpu: bool = False,
    ):

        ta_names = list(df_results["ta_name"].unique())
        if df_results_cpu is not None:
            ta_names_cpu = list(df_results_cpu["ta_name"].unique())
            ta_names += ta_names_cpu
            ta_names = list(set(ta_names))
        ta_names = [c for c in ta_names if c != np.nan]

        df_results_configs_lst = []
        for method_key in ta_names:
            metadata = self.method_metadata(method=method_key)
            if metadata.method_type == "config":
                df_results_configs_lst.append(self.load_config_results(method_key))
        df_results_configs = pd.concat(df_results_configs_lst, ignore_index=True)

        evaluate_all(
            df_results=df_results,
            df_results_holdout=df_results_holdout,
            df_results_cpu=df_results_cpu,
            df_results_configs=df_results_configs,
            configs_hyperparameters=configs_hyperparameters,
            eval_save_path=save_path,
            elo_bootstrap_rounds=elo_bootstrap_rounds,
            use_latex=use_latex,
            realmlp_cpu=realmlp_cpu,
        )

    def find_missing(self, method: str):
        metadata = self.method_metadata(method=method)
        repo = EvaluationRepository.from_dir(path=metadata.path_processed)

        tasks = repo.tasks()
        n_tasks = len(tasks)
        print(f"Method: {method} | n_tasks={n_tasks}")

        metrics = repo.metrics()
        metrics = metrics.reset_index(drop=False)

        configs = repo.configs()

        n_configs = len(configs)

        runs_missing_lst = []

        fail_dict = {}
        for i, config in enumerate(configs):
            metrics_config = metrics[metrics["framework"] == config]
            n_tasks_config = len(metrics_config)

            tasks_config = list(metrics_config[["dataset", "fold"]].values)
            tasks_config = set([tuple(t) for t in tasks_config])

            n_tasks_missing = n_tasks - n_tasks_config
            if n_tasks_missing != 0:
                tasks_missing = [t for t in tasks if t not in tasks_config]
            else:
                tasks_missing = []

            for dataset, fold in tasks_missing:
                runs_missing_lst.append(
                    (dataset, fold, config)
                )

            print(f"{n_tasks_missing}\t{config}\t{i + 1}/{n_configs}")
            fail_dict[config] = n_tasks_missing

        import pandas as pd
        # fail_series = pd.Series(fail_dict).sort_values()

        df_missing = pd.DataFrame(data=runs_missing_lst, columns=["dataset", "fold", "framework"])
        df_missing = df_missing.rename(columns={"framework": "method"})
        print(df_missing)

        # save_pd.save(path="missing_runs.csv", df=df_missing)

        return df_missing

    @classmethod
    def fillna_metrics(cls, df_to_fill: pd.DataFrame, df_fillna: pd.DataFrame) -> pd.DataFrame:
        """
        Fills missing (dataset, fold, framework) rows in df_to_fill with the (dataset, fold) row in df_fillna.

        Parameters
        ----------
        df_to_fill
        df_fillna

        Returns
        -------

        """

        method_col = "method"
        split_col = "fold"
        dataset_col = "dataset"

        columns_to_keep = [
            "method_type",
            "method_subtype",
            "config_type",
            "ta_name",
            "ta_suite",
        ]
        columns_to_keep = [c for c in columns_to_keep if c in df_to_fill]
        per_column: dict[str, dict] = {}
        for c in columns_to_keep:
            groupby_method = df_to_fill.groupby(method_col)[c]
            nunique = groupby_method.nunique(dropna=False)
            invalid = nunique[nunique != 1]
            df_to_fill_invalid = df_to_fill[df_to_fill[method_col].isin(invalid.index)]
            groupby_method_invalid = df_to_fill_invalid.groupby(method_col)[c]
            if not invalid.empty:
                raise AssertionError(
                    f"Found a method with multiple values for column {c} (must be unique):\n"
                    f"{groupby_method_invalid.value_counts()}"
                )

            # Using .first() is safe because nunique == 1 for every method
            per_column[c] = groupby_method.first().to_dict()

        df_to_fill = df_to_fill.set_index([dataset_col, split_col, method_col], drop=True)
        df_fillna = df_fillna.set_index([dataset_col, split_col], drop=True).drop(columns=[method_col])

        unique_frameworks = list(df_to_fill.index.unique(level=method_col))

        df_filled = df_fillna.index.to_frame().merge(
            pd.Series(data=unique_frameworks, name=method_col),
            how="cross",
        )
        df_filled = df_filled.set_index(keys=list(df_filled.columns))

        # missing results
        nan_vals = df_filled.index.difference(df_to_fill.index)

        # fill valid values
        fill_cols = list(df_to_fill.columns)
        df_filled[fill_cols] = np.nan
        df_filled[fill_cols] = df_filled[fill_cols].astype(df_to_fill.dtypes)
        df_filled.loc[df_to_fill.index] = df_to_fill

        df_fillna_to_use = df_fillna.loc[nan_vals.droplevel(level=method_col)].copy()
        df_fillna_to_use.index = nan_vals
        df_filled.loc[nan_vals] = df_fillna_to_use

        if "imputed" not in df_filled.columns:
            df_filled["imputed"] = False
        df_filled.loc[nan_vals, "imputed"] = True

        df_filled = df_filled.reset_index(drop=False)

        # Overwrite values column-by-column while preserving order
        for c in columns_to_keep:
            mapping = per_column[c]
            df_filled[c] = df_filled[method_col].map(mapping)

        return df_filled
