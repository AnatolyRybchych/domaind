
include $(CURDIR)/rules.mk

daemon_name	:= domaind
daemon_bin	:= /usr/local/bin/$(daemon_name)
daemon_dir	:= $(dir $(daemon_bin))

default_config	:= /etc/$(daemon_name)/$(daemon_name).json

define SYSTEMD_SERVICE
[Unit]
Description=domaind
After=network.target

[Service]
Environment="CF_ZONE_ID=XXXXXXXXXXXXXXX" "CF_RECORD_ID=XXXXXXXXXXXXXXX" "CF_API_TOKEN=XXXXXXXXXXXXXXX"
ExecStart=$(daemon_bin) --config $(default_config)
WorkingDirectory=$(daemon_dir)
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
endef

define DEB_CONTROL
Package: $(daemon_name)
Version: 1.0
Section: utils
Priority: optional
Depends: python3
Architecture: amd64
Maintainer: Anatolii Rybchych <tol.ryb@gmail.com>
Description: Simple domain refresh service
endef

define INSTALL_TO
	$(INSTALL) -d "$(1)$(daemon_dir)"
	$(INSTALL) -Dm 755 domaind.py "$(1)$(daemon_bin)"
	$(INSTALL) -d "$(dir $(1)$(default_config))"
	[ -f "$(1)$(default_config)" ] || $(INSTALL) -Dm 644 config.json "$(1)$(default_config)"

	$(file >service,$(call SYSTEMD_SERVICE))
	$(INSTALL) -d "$(1)/etc/systemd/system/"
	$(INSTALL) -Dm 644 service "$(1)/etc/systemd/system/domaind.service"
	$(RM) -f service
endef

all: package deb

package: domaind.py
	$(INSTALL) -d pkg/
	$(call INSTALL_TO,pkg/)

deb: package
	$(INSTALL) -d deb/etc/systemd/system
	$(CP) -Rf pkg/* deb/

	$(INSTALL) -d deb/DEBIAN
	$(file >control,$(call DEB_CONTROL))
	$(INSTALL) -Dm 644 control deb/DEBIAN/control
	$(INSTALL) -d deb/DEBIAN
	$(RM) -f control

	dpkg-deb --build deb && mv -f deb.deb $(daemon_name).deb

install:
	$(call INSTALL_TO,)

clean:
	$(RM) -Rf deb pkg service control deb.deb domaind.deb

.PHONY: all deb clean install
