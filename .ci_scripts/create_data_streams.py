import os
from ooi_harvester.producer import perform_estimates
from ooi_harvester.utils.github import get_gh

import yaml
from datetime import datetime
import textwrap

from pathlib import Path

from ooi_harvester.config import GH_DATA_ORG, CONFIG_PATH_STR, GH_MAIN_BRANCH

instrument_directory_name = 'instruments'


def print_rate_limiting_info(gh, user):
    # Compute some info about our GitHub API Rate Limit.
    # Note that it doesn't count against our limit to
    # get this info. So, we should be doing this regularly
    # to better know when it is going to run out. Also,
    # this will help us better understand where we are
    # spending it and how to better optimize it.

    # Get GitHub API Rate Limit usage and total
    gh_api_remaining = gh.get_rate_limit().core.remaining
    gh_api_total = gh.get_rate_limit().core.limit

    # Compute time until GitHub API Rate Limit reset
    gh_api_reset_time = gh.get_rate_limit().core.reset
    gh_api_reset_time -= datetime.utcnow()

    print("")
    print("GitHub API Rate Limit Info:")
    print("---------------------------")
    print("token: ", user)
    print(
        "Currently remaining {remaining} out of {total}.".format(
            remaining=gh_api_remaining, total=gh_api_total
        )
    )
    print("Will reset in {time}.".format(time=gh_api_reset_time))
    print("")
    return gh_api_remaining


def repo_exists(gh, organization, name):
    # Use the organization provided.
    org = gh.get_organization(organization)
    try:
        org.get_repo(name)
        return True
    except Exception as e:
        if e.status == 404:
            return False
        raise


def list_metas():
    if os.path.isdir(instrument_directory_name):
        metas = os.listdir(instrument_directory_name)
    else:
        metas = []

    for meta_dir in metas:
        # We don't list the "example" feedstock. It is an example, and is there
        # to be helpful.
        # .DS_Store is created by macOS to store custom attributes of its
        # containing folder.
        if meta_dir in ['example', '.DS_Store', '.ipynb_checkpoints', 'test']:
            continue
        path_str = os.path.join(instrument_directory_name, meta_dir)
        path = os.path.abspath(path_str)
        yield path, path_str


def get_config_json(st, meta_dict):
    config_json = {
        'instrument': '',
        'stream': {'method': '', 'name': ''},
        'assigness': [],
        'harvest_options': {},
        'workflow_config': {'schedule': '0 0 * * *'},
    }
    config_json['instrument'] = st['reference_designator']
    config_json['stream']['method'] = st['method']
    config_json['stream']['name'] = st['stream']
    # TODO: need check on values here!
    config_json['harvest_options'] = meta_dict['harvest_config']
    config_json['harvest_options']['path'] = meta_dict['output']['target'][
        'urlpath'
    ]

    return config_json


if __name__ == '__main__':
    exit_code = 0
    gh = get_gh()
    template_repo = gh.get_repo(os.path.join(GH_DATA_ORG, 'stream_template'))
    this_repo = gh.get_repo(os.path.join(GH_DATA_ORG, 'staged-harvest'))
    for meta_path, path_str in list_metas():
        print_rate_limiting_info(gh, 'GH_PAT')
        meta_file = Path(meta_path).joinpath('meta.yaml')
        if meta_file.exists():
            # TODO: Add meta check here!
            meta_dict = yaml.load(meta_file.open(), Loader=yaml.SafeLoader)

            # Get estimate requests
            instrument_name = meta_dict['instrument']['name']
            existing_data_path = meta_dict['output']['target']['urlpath']
            refresh = meta_dict['harvest_config']['refresh']
            success_requests = perform_estimates(
                instrument_name, refresh, existing_data_path
            )

            for request in success_requests:
                stream = request['stream']
                name = stream['table_name']
                config_json = get_config_json(stream, meta_dict)
                if not repo_exists(gh, GH_DATA_ORG, name):
                    print(
                        "Creating ",
                        os.path.join(GH_DATA_ORG, name),
                        "repository ...",
                    )
                    org = gh.get_organization(GH_DATA_ORG)
                    repo = org.create_repo_from_template(
                        name=name,
                        repo=template_repo,
                        description=f"{stream['stream_type']} | {stream['stream_content']}",
                        private=False,
                    )
                    # Update config yaml file
                    contents = repo.get_contents(
                        CONFIG_PATH_STR, ref=GH_MAIN_BRANCH
                    )
                    repo.update_file(
                        CONFIG_PATH_STR,
                        message="Updating stream configuration",
                        content=yaml.dump(config_json),
                        sha=contents.sha,
                        branch=GH_MAIN_BRANCH,
                    )

                    # TODO: Retrieve More Stream/Instrument descriptions/text
                    readme_text = textwrap.dedent(
                        f"""\
                    # {stream['table_name']}

                    Stream Type: {stream['stream_type']}<br>
                    Stream Content: {stream['stream_content']}<br>
                    Instrument Group Code: {stream['group_code']}<br>
                    """
                    )

                    README_PATH_STR = 'README.md'
                    readme = repo.get_contents(
                        README_PATH_STR, ref=GH_MAIN_BRANCH
                    )
                    repo.update_file(
                        README_PATH_STR,
                        message="Updating readme text",
                        content=readme_text,
                        sha=readme.sha,
                        branch=GH_MAIN_BRANCH,
                    )

                    # Dispatching workflow
                    data_request = repo.get_workflow('data-request.yaml')
                    data_request.create_dispatch(GH_MAIN_BRANCH)
                    print("DONE. Data request workflow has been dispatched.")
                else:
                    print(
                        os.path.join(GH_DATA_ORG, name), "exists! Skipping ..."
                    )

        # Cleaning after success
        meta_contents = this_repo.get_contents(
            os.path.join(path_str, 'meta.yaml'), ref=GH_MAIN_BRANCH
        )
        template_repo.delete_file(
            meta_contents.path,
            message=f'Clean up {meta_contents.path}',
            sha=meta_contents.sha,
            branch=GH_MAIN_BRANCH,
        )
