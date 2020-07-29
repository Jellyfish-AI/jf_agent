# jf_agent

An agent that can run on-premise to download and send data to [Jellyfish](https://jellyfish.co/).

## Usage

The agent has several different usage modes:

1. Download Jira and/or Git data from your systems; send the resulting data to Jellyfish (`download_and_send`).

2. Download Jira and/or Git data from your systems. Allow the downloaded data to be inspected before it's sent to Jellyfish (`download_only`).

3. Send a previously downloaded dataset to Jellyfish (`send_only`).

4. Show the keys and field names for all of your Jira custom fields (to aid in agent configuration) (`print_all_jira_fields`).

5. Show the names and urls of Git repositories that may be missing from Jellyfish by looking at the Development Jira custom field (to aid in agent configuration)(`print_apparently_missing_git_repos`).

Data that you download from Jira and/or Git may be scrubbed to remove sensitive fields and values before you send it to Jellyfish.

## Installation / Configuration

The agent is distributed as a Docker image. The image bundles the agent's source code, a Python 3 environment, and the AWS command line tools.

You'll execute the agent by running a Docker container based on the distributed image. You'll configure the agent by writing a YAML configuration file and by providing a set of environment variables that contain your relevant credentials.

### Prerequisites

1. Obtain your API token from Jellyfish.

2. For Jira: Gather your Jira credentials. You'll need a Jira username with read access to the right projects, along with the password for that user (for Jira server) or a personal API token for that user (for Jira cloud).

3. For Bitbucket Server: Gather your Bitbucket Server credentials.

4. For Bitbucket Cloud: Create an app password (https://confluence.atlassian.com/bitbucket/app-passwords-828781300.html#Apppasswords-Createanapppassword).

5. For GitHub: Create a personal access token (https://help.github.com/en/articles/creating-a-personal-access-token-for-the-command-line).

6. For GitLab: Create a personal access token (https://docs.gitlab.com/ee/user/profile/personal_access_tokens.html#creating-a-personal-access-token)

### Configuration

1. Create a YAML config file to tell the agent how to run. You can base this on our [example config file](https://github.com/Jellyfish-AI/jf_agent/blob/master/example.yml). Save the YAML file on the host that will execute the agent.

2. Create a file that contains the credentials you gathered above, specified in the [environment variable syntax](https://docs.docker.com/engine/reference/commandline/run/#set-environment-variables--e---env---env-file). The file should look like this:

#### For Jira and Single Git Mode:
```
JELLYFISH_API_TOKEN=...
JIRA_USERNAME=...
JIRA_PASSWORD=...
GITHUB_TOKEN=...
```

For Jira and Bitbucket Server:
```
JELLYFISH_API_TOKEN=...
JIRA_USERNAME=...
JIRA_PASSWORD=...
BITBUCKET_USERNAME=...
BITBUCKET_PASSWORD=...
```

For Jira and Bitbucket Cloud:
```
JELLYFISH_API_TOKEN=...
JIRA_USERNAME=...
JIRA_PASSWORD=...
BITBUCKET_CLOUD_USERNAME=...
BITBUCKET_CLOUD_APP_PASSWORD=...
```

For Jira and GitLab:
```
JELLYFISH_API_TOKEN=...
JIRA_USERNAME=...
JIRA_PASSWORD=...
GITLAB_TOKEN=...
```

#### Multiple Git Instances
If using multiple git instances, all git credentials must be prefixed with the corresponding `creds_envvar_prefix` provided in the config.yml.

For example, if the  `creds_envvar_prefix` is set to ORG1 for a bitbucket instance, the configuration would include the following variables:
```
# ... other variables
ORG1_BITBUCKET_USERNAME=...
ORG1_BITBUCKET_PASSWORD=...
```
When in multi-git mode, `creds_envvar_prefix` is required for all git instances. See the example.yml for more details.

## Execution

You execute the agent with a `docker run` command that references the image on Docker Hub. You'll use bind mounts and environment variables to configure it with your YAML file and credentials.

You can pull down the latest Docker image from Docker Hub with:
```
docker pull jellyfishco/jf_agent:latest
```

You may also want to periodically perform that `docker pull` command, or prepend it to the command you use for `docker run`, to ensure you're using the latest version of the agent.

### Execution variants

#### Specifying a usage mode

The usage mode is provided to the agent via the `-m` argument. The value should be one of: `download_and_send`, `download_only`, `send_only`, `print_all_jira_fields`, `print_apparently_missing_git_repos`. If you don't provide a `-m` argument, the `download_and_send` mode is used.

#### Providing YAML configuration file as bind mount

The YAML configuration file you've created should be provided to the container via a bind mount. The syntax for providing a bind mount is:

```
--mount type=bind,source=<host_path>,target=<container_path>
```

The `host_path` should be the full path to where you've stored the YAML configuration file. The `container_path` must be `/home/jf_agent/config.yml`.

#### Providing credentials as environment variables

Your credentials should be provided to the container via environment variables. The syntax for providing environment variables from a file is:

```
--env-file <full_path_to_env_file>
```

#### Saving the downloaded output

By default, the agent will download and send the data it collects. Upon completion the data downloaded will be stored inside the container. If you use the `--rm` argument to `docker run` then the container and the data will be cleaned up when the agent completes.

If you instead want to save the downloaded output (perhaps so that you can inspect it), you can provide a bind mount that maps a host directory to the container's agent output directory.

As for providing the YAML configuration file, the syntax for providing a bind mount for the agent output directory is:
```
--mount type=bind,source=<host_path>,target=<container_path>
```

In this case, the `host_path` should be the full path to a directory on the host and the `container_path` must be `/home/jf_agent/output`.

#### Specifying a previously downloaded dataset to be sent

If you've run the agent in `download_only` mode so that you can inspect its output, when you're ready to send the data to Jellyfish you'll use the `send_only` mode. You'll provide a bind mount for the output directory, and you'll also provide the `-od` argument to specify a path relative to the container's output directory that contains the data previously downloaded.

When the agent runs, it saves its downloaded data in a timestamped directory inside of `/home/jf_agent/output`. It shows the directory its downloaded data is being written to with a line like this:
```
Will write output files into ./output/20190822_133513
```

So, e.g., if an earlier run with `download_only` may has written its output file into `./output/20190822_133513` and the host directory `/tmp/jf_agent/output` had been mounted at `/home/jf_agent/output`, you'd use these arguments to send that data to Jellyfish:

```
--mount type=bind,source=/tmp/jf_agent_output,target=/home/jf_agent/output
-m send_only
-od ./output/20190822_133513
```

### Sample execution commands

The following sample commands can be used for common usage scenarios.

1. Normal mode: download and send
```
docker pull jellyfishco/jf_agent:latest && \
docker run --rm \
--mount type=bind,source=/full/path/ourconfig.yml,target=/home/jf_agent/config.yml \
--env-file /full/path/creds.env \
jellyfishco/jf_agent:latest
```

2. Download data without sending
```
docker pull jellyfishco/jf_agent:latest &&
docker run --rm \
--mount type=bind,source=/full/path/ourconfig.yml,target=/home/jf_agent/config.yml \
--mount type=bind,source=/full/path/jf_agent_output,target=/home/jf_agent/output \
--env-file ./creds.env \
jellyfishco/jf_agent:latest -m download_only
```

3. Send previously downloaded data
```
docker pull jellyfishco/jf_agent:latest &&
docker run --rm \
--mount type=bind,source=/full/path/ourconfig.yml,target=/home/jf_agent/config.yml \
--mount type=bind,source=/full/path/jf_agent_output,target=/home/jf_agent/output \
--env-file ./creds.env \
jellyfishco/jf_agent:latest -m send_only -od ./output/20190822_133513
```

4. Print info on Jira fields
```
docker pull jellyfishco/jf_agent:latest &&
docker run --rm \
--mount type=bind,source=/full/path/ourconfig.yml,target=/home/jf_agent/config.yml \
--env-file ./creds.env \
jellyfishco/jf_agent:latest -m print_all_jira_fields
```

5. Print Git repos apparently missing from Jellyfish
```
docker pull jellyfishco/jf_agent:latest &&
docker run --rm \
--mount type=bind,source=/full/path/ourconfig.yml,target=/home/jf_agent/config.yml \
--env-file ./creds.env \
jellyfishco/jf_agent:latest -m print_apparently_missing_git_repos
```

## Jira Fields

It is possible to configure the agent to pull a subset of fields from
Jira.  This can be useful if, for example, certain fields contain
sensitive data that you don't want to send to Jellyfish.

This can be controlled through the `include_fields` and `exclude_fields`
options in the config file.  Note, however, that certain fields are required in order
for Jellyfish to work.  These required fields are:

```
issuekey
parent
issuelinks
project
reporter
assignee
creator
issuetype
resolution
resolutiondate
status
created
updated
subtasks
```

Some of the Jira agile feature are built internally on "custom fields" that Jellyfish uses. These
custom fields have keys in the form `customfield_XXXXX`, but where the digits represented by X
are different in each Jira installation. You can find the keys for your custom
fields by running the agent in the `print_all_jira_fields` mode.  The custom
fields used by Jellyfish are the following:

```
Epic Link
Epic Name
Sprint
Parent Link
Story Points
Rank
```

Make sure that at least these fields are configured for Jellyfish to pull.

Note that the `print_apparently_missing_git_repos` mode requires that Jellyfish have access to the
Development custom field and that we have already processed your data with this field included.

Additional Jellyfish functionality is enabled if the following fields are pulled:
```
summary
description
priority
worklog
comment
timetracking
duedate
labels
fixVersions
versions
components
timeestimate
timeoriginalestimate
timespent
aggregatetimespent
aggregatetimeoriginalestimate
aggregatetimeestimate
```
