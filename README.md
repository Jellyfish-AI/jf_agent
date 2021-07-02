# jf_agent

An agent that can run on-premise to download and send data to [Jellyfish](https://jellyfish.co/).

If you are looking for details on how to set up your agent, look [here](#setup-guide)!

## Table of Contents

**[Setup Guide](#setup-guide)** :hammer_and_wrench:  - *A step-by-step guide for setting up the Jellyfish agent*

**[Running the agent](#running-the-agent)** :running: - *A guide on how to run the agent*
- **[Keep the agent updated](#keep-the-agent-updated)** :date: - *How to keep your agent code up to date*
- **[Specify a usage mode](#specify-a-usage-mode)** :computer: - *Specify how you would like to use the agent*
- **[Save the downloaded output](#save-the-downloaded-output)** :inbox_tray: - *How to save the agent's output*
- **[Specify a previously downloaded dataset to be sent](#specify-a-previously-downloaded-dataset-to-be-sent)** :recycle: - *How to send downloaded data to Jellyfish*
- **[Sample execution commands](#sample-execution-commands)** :books: - *Examples of common ways to run the agent*

**[Usage Modes](#usage-modes)** :mag: - *Information on the different usage modes that the agent offers*

**[Specify Jira Fields](#specify-jira-fields)** :memo: - *Configure the agent to send only certain Jira fields to Jellyfish*

## Setup Guide :hammer_and_wrench:

1. **Create a YAML configuration file**
   - This will help our agent work with your applications!
   - You can use this [example config file](https://github.com/Jellyfish-AI/jf_agent/blob/master/example.yml) to get started. Using this example file, fill out fields such as your Jira url, and Git url. Uncomment configuration options as desired.
   - Save the YAML file on the host that will execute the agent.  

2. **Create an environment variable file**
    - This is where you'll store authentication information, such as your API tokens, and login information
    - The way your file looks will vary based on your organization's tool set. The base file should look something like the code snippet below, with the corresponding values filled in in place of the ellipses:
      ```
      JELLYFISH_API_TOKEN=...      
      JIRA_USERNAME=...
      JIRA_PASSWORD=...
      ```
      - You can obtain the value for `JELLYFISH_API_TOKEN` from Jellyfish
      - To get a value for `JIRA_USERNAME`, choose a Jira username that has read access to all of the projects you would like to use in Jellyfish
      - The value for `JIRA_PASSWORD` will vary. If you are using Jira server, enter the password for the user specified with `JIRA_USERNAME`. If you are using Jira Cloud, create a personal API token, following the instructions [here](https://support.atlassian.com/atlassian-account/docs/manage-api-tokens-for-your-atlassian-account/) to obtain one.
   - To accomodate for additional tooling, add the following lines specified below in addition to the base values above:
      - ### **For GitHub users**:  
         Add the following to your environment variable file:  
         ```
         GITHUB_TOKEN=...
         ```
         - Create a personal access token in Github, following the instructions [here](https://docs.github.com/en/github/authenticating-to-github/keeping-your-account-and-data-secure/creating-a-personal-access-token). Use this token as the value for `GITHUB_TOKEN`
      - ### **For GitLab users**:  
         Add the following to your environment variable file:  
         ```
         GITLAB_TOKEN=...
         ```
         - Create a personal access token in GitLab, following the instructions [here](https://docs.gitlab.com/ee/user/profile/personal_access_tokens.html#creating-a-personal-access-token). Use this token as the value for `GITLAB_TOKEN`
      - ### **For Bitbucket Server users**:  
         Add the following to your environment variable file:  
         ```
         BITBUCKET_USERNAME=...
         BITBUCKET_PASSWORD=...
         ```
         - `BITBUCKET_USERNAME` should be your Bitbucket server's username
         - `BITBUCKET_PASSWORD` should be your Bitbucket server's password
      - ### **For Bitbucket Cloud users**:  
         Add the following to your environment variable file:  
         ```
         BITBUCKET_CLOUD_USERNAME=...
         BITBUCKET_CLOUD_APP_PASSWORD=...
         ```
         - `BITBUCKET_CLOUD_USERNAME` should be your Bitbucket cloud username
         - Create an app password in Bitbucket, following the instructions [here](https://support.atlassian.com/bitbucket-cloud/docs/app-passwords/#Apppasswords-Createanapppassword). Use this app password as the value for `BITBUCKET_CLOUD_APP_PASSWORD`
      - ### **For users with multiple Git instances**:  
         If using multiple git instances, all git credentials must be prefixed with the corresponding `creds_envvar_prefix` provided in the `config.yml`.

         For example, if the `creds_envvar_prefix` is set to `ORG1` for a bitbucket instance, the configuration would include the following variables:
         ```
         ORG1_BITBUCKET_USERNAME=...
         ORG1_BITBUCKET_PASSWORD=...
         ```
         When in multi-git mode, creds_envvar_prefix is required for all git instances. See the `example.yml` from Step 1 of this [Setup guide](#setup-guide) for more details.  

3. **Ensure proper network configuration**
   - The agent will need to make various requests to function. Thus, we ask that you please ensure that your network firewall/proxies are configured such that the agent is able to:
      - Make GET requests to the Jellyfish API at https://app.jellyfish.co:443/
      - Make GET requests to your Jira and Git host(s) on port 443
      - Make POST requests to URLs under s3.amazonaws.com on port 443


## Running the agent :running:

The agent is distributed as a Docker image. The image bundles the agent's source code, a Python 3 environment, and the AWS command line tools.

Execute the agent with a `docker run` command that references the image on Docker Hub. You'll use bind mounts and environment variables to configure it with your YAML file and credentials.

- The YAML configuration file you've created should be provided to the container via a bind mount. The syntax for providing a bind mount is: 
   ```
   --mount type=bind,source=<host_path>,target=<container_path>
   ```

   The `host_path` should be the full path to where you've stored the YAML configuration file. The `container_path` must be `/home/jf_agent/config.yml`.
- Your credentials should be provided to the container via environment variables. The syntax for providing environment variables from a file is:

   ```
   --env-file <full_path_to_env_file>
   ```

### Specify a usage mode :computer:

The usage mode is provided to the agent via the `-m` argument. The value should be one of: `download_and_send`, `download_only`, `send_only`, `print_all_jira_fields`, `print_apparently_missing_git_repos`. If you don't provide a `-m` argument, the `download_and_send` mode is used.  

You can see more details about the various usage modes [here](#usage-modes).

### Keep the agent updated :date:

You can pull down the latest Docker image from Docker Hub with:
```
docker pull jellyfishco/jf_agent:stable
```

You may also want to periodically perform that `docker pull` command, or prepend it to the command you use for `docker run`, to ensure you're using the latest version of the agent.

### Save the downloaded output :inbox_tray:

By default, the agent will download and send the data it collects. Upon completion the data downloaded will be stored inside the container. If you use the `--rm` argument to `docker run` then the container and the data will be cleaned up when the agent completes.

If you instead want to save the downloaded output (perhaps so that you can inspect it), you can provide a bind mount that maps a host directory to the container's agent output directory.

Just like for providing the YAML configuration file, the syntax for providing a bind mount for the agent output directory is:
```
--mount type=bind,source=<host_path>,target=<container_path>
```

In this case, the `host_path` should be the full path to a directory on the host and the `container_path` must be `/home/jf_agent/output`.

### Specify a previously downloaded dataset to be sent :recycle:

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

### Sample execution commands :books:

The following sample commands can be used for common usage scenarios.

1. Normal mode: download and send
```
docker pull jellyfishco/jf_agent:stable && \
docker run --rm \
--mount type=bind,source=/full/path/ourconfig.yml,target=/home/jf_agent/config.yml \
--env-file /full/path/creds.env \
jellyfishco/jf_agent:stable
```

2. Download data without sending
```
docker pull jellyfishco/jf_agent:stable &&
docker run --rm \
--mount type=bind,source=/full/path/ourconfig.yml,target=/home/jf_agent/config.yml \
--mount type=bind,source=/full/path/jf_agent_output,target=/home/jf_agent/output \
--env-file ./creds.env \
jellyfishco/jf_agent:stable -m download_only
```

3. Send previously downloaded data
```
docker pull jellyfishco/jf_agent:stable &&
docker run --rm \
--mount type=bind,source=/full/path/ourconfig.yml,target=/home/jf_agent/config.yml \
--mount type=bind,source=/full/path/jf_agent_output,target=/home/jf_agent/output \
--env-file ./creds.env \
jellyfishco/jf_agent:stable -m send_only -od ./output/20190822_133513
```

4. Print info on Jira fields
```
docker pull jellyfishco/jf_agent:stable &&
docker run --rm \
--mount type=bind,source=/full/path/ourconfig.yml,target=/home/jf_agent/config.yml \
--env-file ./creds.env \
jellyfishco/jf_agent:stable -m print_all_jira_fields
```

5. Print Git repos apparently missing from Jellyfish
```
docker pull jellyfishco/jf_agent:stable &&
docker run --rm \
--mount type=bind,source=/full/path/ourconfig.yml,target=/home/jf_agent/config.yml \
--env-file ./creds.env \
jellyfishco/jf_agent:stable -m print_apparently_missing_git_repos
```

6. Validate configuration
```
docker pull jellyfishco/jf_agent:stable &&
docker run --rm \
--mount type=bind,source=/full/path/ourconfig.yml,target=/home/jf_agent/config.yml \
--env-file ./creds.env \
jellyfishco/jf_agent:stable -m validate
```

## Usage Modes :mag:

The agent has several different usage modes:

1. Download Jira and/or Git data from your systems; send the resulting data to Jellyfish (`download_and_send`).

2. Download Jira and/or Git data from your systems. Allow the downloaded data to be inspected before it's sent to Jellyfish (`download_only`).

3. Send a previously downloaded dataset to Jellyfish (`send_only`).

4. Show the keys and field names for all of your Jira custom fields (to aid in agent configuration) (`print_all_jira_fields`).

5. Show the names and urls of Git repositories that may be missing from Jellyfish by looking at the Development Jira custom field (to aid in agent configuration)(`print_apparently_missing_git_repos`).

6. Validate the configuration file using APIs (`validate`).

Data that you download from Jira and/or Git may be scrubbed to remove sensitive fields and values before you send it to Jellyfish.

## Specify Jira Fields :memo:

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
