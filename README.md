# MAD Responder [![Picture](https://raw.github.com/janelia-flyem/janelia-flyem.github.com/master/images/HHMI_Janelia_Color_Alternate_180x40.png)](http://www.janelia.org)

[![Build Status](https://travis-ci.org/janelia-flyem/mad-responder.svg?branch=master)](https://travis-ci.org/janelia-flyem/mad-responder)
[![GitHub last commit](https://img.shields.io/github/last-commit/janelia-flyem/mad-responder.svg)](https://github.com/janelia-flyem/mad-responder)
[![GitHub commit merge status](https://img.shields.io/github/commit-status/badges/shields/master/5d4ab86b1b5ddfb3c4a70a70bd19932c52603b8c.svg)](https://github.com/janelia-flyem/mad-responder)
[![Python 3.7](https://img.shields.io/badge/python-3.7-blue.svg)](https://www.python.org/downloads/release/python-360/)
[![Requirements Status](https://requires.io/github/janelia-flyem/mad-responder/requirements.svg?branch=master)](https://requires.io/github/janelia-flyem/mad-responder/requirements/?branch=master)
[![Coverage](https://img.shields.io/codecov/c/github/janelia-flyem/mad-responder.svg)](https://codecov.io/gh/janelia-flyem/mad-responder)

## Summary
This Python Flask app provides REST API endpoints for the MAD database. 

## Configuration

This system depends on the [Centralized Config](https://github.com/JaneliaSciComp/Centralized_Config) system, and
will use the following configurations:
- rest_services
- servers

The location of the configuration system is in the config.cfg file as CONFIG_ROOT.

## Deployment

After installing on the production server, take the following steps to start the system:
```
cd /opt/flask/mad-responder
sudo systemctl start gunicorn
sudo systemctl start nginx
```

## Development
1. Create and activate a clean Python 3 environment:
    ```
    python3 -m venv myenv
    source myenv/bin/activate
    ```
1. Install dependencies:

    `pip3 install -r requirements.txt`
1. Run tests:

    `python3 test_base.py`
1. Start server:

    `python3 mad_responder.py`
1. When you're done, deactivate the virtual environment:

    `deactivate`

## Author Information
Written by Rob Svirskas (<svirskasr@janelia.hhmi.org>)

[Scientific Computing](http://www.janelia.org/research-resources/computing-resources)  
