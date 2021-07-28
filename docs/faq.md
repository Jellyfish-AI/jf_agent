---
title: FAQ
layout: basic-page
pageDescription: On this page, you will find the answers to some common questions about the agent.
---

## When setting up an Agent, what are the recommended CPU/memory/storage specs?  

2+GB of RAM, 20+GB of storage, 2+ cores  

The standard we usually go off of is >=2GB of RAM. In terms of storage, this varies a bit as it can be hard to predict how much storage you may need, but we've never seen >20GB. Aside from those, any amount of CPU should work, but if you need a minimum, set it for 2 CPU cores.  

If you're using kubernetes, run the agent as a kubernetes job, as explained [here](https://kubernetes.io/docs/concepts/workloads/controllers/cron-jobs/).  


## Does the Agent auto-update, is there a way to initiate a manual update of the agent, or does it require a deploy of an updated docker image?  

Clients can auto-update the Agent before every run. In the cron job/automated run, you can run a docker pull to get the latest version of the Agent.  

You can refer to the full agent updating instructions [here](agent-updated.html).  


## Is there a communications channel that lets us know when an agent update is available? Are release log details such as features additions, security fixes, etc. included?  

A member of Jellyfish’s Product Success team will reach out with notifications to update the Agent. Logs are captured by the client and can be reviewed at any time.  


## How should we have the agent run once set up?  

Usually utilizing a k8s cronjob, most clients utilize a once or twice a day cadence for their agent configuration. The agent will exit after it’s done its initial pull and we’d recommend not having it restart immediately after completion. The agent checks the most recent timestamp of commit and only pulls in items after that point. But if we don't have a most recent timestamp yet (our whole processing pipeline needs to complete), we'll run through all repos again like it never happened.  

See more documentation on how to run the agent [here](run-agent.html)  


## Can we cherry-pick certain Epics go into Jellyfish via the Agent?

Clients can select which Projects and fields to send to Jellyfish.  

You can learn more about the Jira fields that can be specified [here](jira-fields.html).  

