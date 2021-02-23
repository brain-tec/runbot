FROM ubuntu:bionic
ENV LANG C.UTF-8
USER root

# Install debian packages
RUN set -x ; \
    apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends apt-transport-https build-essential ca-certificates curl ffmpeg file fonts-freefont-ttf fonts-noto-cjk gawk gnupg libldap2-dev libsasl2-dev libxslt1-dev lsb-release node-less ocrmypdf sed sudo unzip xfonts-75dpi zip zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

# Install debian packages
RUN set -x ; \
    apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3 python3-dev python3-pip python3-setuptools python3-wheel python3-markdown libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN set -x ; \
    apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python python-dev python-pip \
    && rm -rf /var/lib/apt/lists/*

# Install wkhtml
RUN curl -sSL https://github.com/wkhtmltopdf/wkhtmltopdf/releases/download/0.12.5/wkhtmltox_0.12.5-1.bionic_amd64.deb -o /tmp/wkhtml.deb \
    && apt-get update \
    && dpkg --force-depends -i /tmp/wkhtml.deb \
    && apt-get install -y -f --no-install-recommends \
    && rm /tmp/wkhtml.deb

# Install nodejs
RUN curl -sSL https://deb.nodesource.com/gpgkey/nodesource.gpg.key | apt-key add - \
    && echo "deb https://deb.nodesource.com/node_8.x stretch main" > /etc/apt/sources.list.d/nodesource.list \
    && apt-get update \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g rtlcss es-check

ADD https://raw.githubusercontent.com/brendangregg/FlameGraph/master/flamegraph.pl /usr/local/bin/flamegraph.pl
RUN chmod +rx /usr/local/bin/flamegraph.pl

ADD https://raw.githubusercontent.com/odoo/odoo/10.0/debian/control /tmp/p2-control
ADD https://raw.githubusercontent.com/odoo/odoo/master/debian/control /tmp/control.txt
RUN apt-get update \
    && sed -n '/^Depends:/,/^[A-Z]/p' /tmp/p2-control /tmp/control.txt \
       | awk '/^ [a-z]/ { gsub(/,/,"") ; print }' | sort -u \
       | egrep -v 'postgresql-client' \
       | sed 's/python-imaging/python-pil/'| sed 's/python-pypdf/python-pypdf2/' \
       | sed -r 's/(python[3]?)-docutils/docutils-common \1-roman/' \
       | DEBIAN_FRONTEND=noninteractive xargs apt-get install -y -qq \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

RUN curl -sSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | apt-key add - \
    && echo "deb http://apt.postgresql.org/pub/repos/apt/ `lsb_release -s -c`-pgdg main" > /etc/apt/sources.list.d/pgclient.list \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y postgresql-client-12 \
    && rm -rf /var/lib/apt/lists/*

# Install phantomjs
RUN curl -sSL https://bitbucket.org/ariya/phantomjs/downloads/phantomjs-2.1.1-linux-x86_64.tar.bz2  -o /tmp/phantomjs.tar.bz2 \
    && tar xvfO /tmp/phantomjs.tar.bz2 phantomjs-2.1.1-linux-x86_64/bin/phantomjs > /usr/local/bin/phantomjs \
    && chmod +x /usr/local/bin/phantomjs \
    && rm -f /tmp/phantomjs.tar.bz2

# Install Google Chrome
RUN curl -sSL http://nightly.odoo.com/odoo.key | apt-key add - \
    && echo "deb http://nightly.odoo.com/deb/bionic ./" > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update \
    && apt-get install -y -qq google-chrome-stable=80.0.3987.116-1 \
    && rm -rf /var/lib/apt/lists/*

# Install extra debian dependencies (needs for some repos)
RUN set -x ; \
    apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends libmagic1 libzbar0 python3-lasso \
    && rm -rf /var/lib/apt/lists/*

# Install Odoo generic dependencies
ADD https://raw.githubusercontent.com/odoo/odoo/10.0/requirements.txt /root/p2-requirements.txt
RUN python2 -m pip install -U setuptools wheel && \
    python2 -m pip install --no-cache-dir -r /root/p2-requirements.txt && \
    python2 -m pip install coverage flanker==0.4.38 pylint==1.7.2 phonenumbers redis && \
    rm -Rf /root/p2-requirements.txt /root/.cache/pip

ADD https://raw.githubusercontent.com/odoo/odoo/master/requirements.txt /root/requirements.txt
RUN python3 -m pip install setuptools wheel && \
    python3 -m pip install --no-cache-dir -r /root/requirements.txt && \
    python3 -m pip install coverage==4.5.4 websocket-client astroid==2.4.2 pylint==2.5.0 phonenumbers pyCrypto dbfread==2.0.7 firebase-admin==2.17.0 flamegraph pdfminer.six==20200720 pdf417gen==0.7.1 && \
    python3 -m pip install --upgrade pip && \
    rm -f /usr/local/bin/pip && \
    rm -Rf /root/requirements.txt /root/.cache/pip
