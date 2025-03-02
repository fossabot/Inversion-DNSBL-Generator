"""
GitHub

Push generated blocklists to GitHub
"""

import os
from typing import Optional

from dotenv import dotenv_values
from modules.filewriter import BLOCKLISTS_FOLDER
from modules.utils.log import init_logger
from modules.utils.types import Vendors

import github

logger = init_logger()


def upload_blocklists(vendor: Vendors, blocklist_filenames: tuple[str, ...], suffix: Optional[str] = None) -> None:
    """Uploads blocklists to GitHub repository

    Args:
        vendor (Vendors): Safe Browsing API vendor name
        (e.g. "Google", "Yandex" etc.)
        blocklist_filenames (tuple[str,...]): Blocklists
        to be uploaded to GitHub
        suffix (Optional[str], optional): Suffix to be added to
        blocklist filenames when uploading to GitHub. Defaults to None.
    """
    try:
        path_list = [f"{BLOCKLISTS_FOLDER}{os.sep}{original_filename}" for original_filename in blocklist_filenames]
        file_names = [
            f"{vendor}_{original_filename.split('_')[1]}{f'_{suffix}' if suffix else ''}.txt" for original_filename in blocklist_filenames
        ]

        access_token = dotenv_values(".env")["GITHUB_ACCESS_TOKEN"]
        repo_name = dotenv_values(".env")["BLOCKLIST_REPOSITORY_NAME"]
        if access_token is None:
            raise ValueError("Access Token missing from environment file")
        if repo_name is None:
            raise ValueError("Blocklist Repository Name missing from environment file")

        g = github.Github(access_token)
        repo = g.get_user().get_repo(repo_name)

        commit_message = f"Update {vendor} blocklists"
        main_ref = repo.get_git_ref("heads/main")
        main_sha = main_ref.object.sha
        base_tree = repo.get_git_tree(main_sha)

        element_list = list()
        for i, entry in enumerate(path_list):
            with open(entry) as input_file:
                data = input_file.read()
            # Do not commit empty files
            if data:
                element = github.InputGitTreeElement(file_names[i], "100644", "blob", data)
                element_list.append(element)

        files_changed: list[github.File.File] = []
        if element_list:
            # create git tree only if there are non-empty files
            tree = repo.create_git_tree(element_list, base_tree)
            parent = repo.get_git_commit(main_sha)
            commit = repo.create_git_commit(commit_message, tree, [parent])
            comparison = repo.compare("main", commit.sha)
            files_changed = comparison.files

        if files_changed:
            # Push commit to main only if there are files to change
            main_ref.edit(commit.sha)
            logger.info("Updated repository with %s blocklists", vendor)
        elif not element_list:
            logger.warning("%s blocklists are empty, won't update repository", vendor)
        else:
            logger.info("No changes found for %s blocklists", vendor)
    except Exception as error:
        logger.warning("Failed to update repository with %s blocklists", vendor)
        logger.warning("%s", repr(error))
