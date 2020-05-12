
[![Build Status](http://runbot.odoo.com/runbot/badge/flat/13/13.0.svg)](http://runbot.odoo.com/runbot)

# Odoo Runbot Repository

This repository contains the source code of Odoo testing bot [runbot.odoo.com](http://runbot.odoo.com/runbot) and related addons.

------------------

## Warnings

**Runbot will delete folders/ drop database to free some space during usage.** Even if only elements create by runbot are concerned, don't use runbot on a server with sensitive data.

**Runbot changes some default odoo behaviours** Runbot database may work with other modules, but without any garantee. Avoid to use runbot on an existing database/install other modules than runbot.

## HOW TO

This section give the basic steps to follow to configure the runbot v5.0. The configuration may differ from one use to another, this one will describe how to test addons for odoo, needing to fetch odoo core but without testing vanilla odoo.

### Setup

Runbot is an addond for odoo, meaning that both odoo and runbot code are needed to run. Some tips to configure odoo are available in [odoo setup documentation](https://www.odoo.com/documentation/13.0/setup/install.html#setup-install-source) (requirements, postgres, ...) This page will mainly focus on runbot specificities.

Chose a workspace and clone both repository.
```
git clone https://github.com/odoo/odoo.git
git clone https://github.com/odoo/runbot.git
```

Runbot dependeds on some odoo version, runbot v5.0 is currently based on odoo 13.0 (Runbot 13.0.5.0). Both runbot and odoo 13.0 branch should be chekouted. *This logic follow the convention imposed by runbot to run code from different repository, the branch name must be the same or be prefixed by a main branch name.*

```
git -C odoo checkout 13.0
git -C runbot checkout 13.0
```

You will also need to install docker on your system

### Install and start runbot

Odoo being an odoo addon, you need to start odoo giving runbot in the addons path. Install runbot by giving the -i instruction.

```
python3 odoo/odoo-bin -d runbot_databse --addons-path odoo/addons,runbot -i runbot --stop-after-init --without-demo=1
```

Then, launch runbot
```
python3 odoo/odoo-bin -d runbot_databse --addons-path odoo/addons,runbot
```

You may want to configure a service or launch odoo in a screen depending on your preferences.

### Configuration

*Note: Runbot is optimized to run commit discovery and build sheduling on different host to allow load share on different machine. This basic configuration will show how to run runbot on a single machine, a less-tested use case*

#### Bootstrap
One launched, the cron should start to do basic work. The commit discovery and buld sheduling is disabled by default, but runbot bootstrap will start to setup some directories in static.
>Starting job `Runbot`.
```
ls runbot/runbot/static
```
>build  docker  nginx  repo  sources  src

- **repo** contains the bare repositories
- **source** contains the exported sources needed for each build
- **source** contains the exported sources needed for each build
- **build** contains the different workspaces for dockers
- **docker** contains DockerFile and corresponding logs
- **nginx** contaings the nginx config used to access running instances
All of them are emply for now.

A database defined by *runbot.runbot_db_template* icp will be created. By default, runbot use template1. This database will be used as template for testing builds. You can change this database for more customisation.


