%{!?python_sitelib: %define python_sitelib %(%{__python} -c "from distutils.sysconfig import get_python_lib; print get_python_lib()")}

Name:           stratosource
Version: 2.6.2
Release: 2
Summary:        Process git repo dumps of salesforce assets and provide web UI for the results

Group:          Applications/Internet
License:        GPL
URL:            http://www.StratoSource.com/
Source0:        %{name}-%{version}-%{release}.tar.gz
BuildRoot:      %{_tmppath}/%{name}-%{version}-%{release}-root

Requires:       python >= 2.6.4
Requires:       python-suds >= 0.3.9
Requires:	python-requests >= 0.13.1
Requires:       memcached >= 1.4.5
Requires:       python-memcached >= 1.47
Requires:       python-lxml >= 2.2.7
Requires:	Django >= 1.3
Requires:       mysql >= 5.1.51
Requires:       mysql-server >= 5.1.51
Requires:       httpd >= 2.2.16
Requires:       mod_wsgi >= 3.1
Requires:       mod_auth_kerb >= 5.4
Requires:       MySQL-python >= 1.2.3
Requires:	wget >= 1.12
#Requires:       subversion >= 1.6.9
Requires:       git >= 1.7.3
Requires:	cgit >= 0.9

BuildArch:      noarch


%description
Process git repo dumps of salesforce assets and provide web UI for the results

%prep
%setup -q

%install
mkdir -p %{buildroot}/usr/django
cp -R stratosource %{buildroot}/usr/django/
cp -R resources %{buildroot}/usr/django/
cp *cronjob.sh %{buildroot}/usr/django/
cp runmanage.sh %{buildroot}/usr/django/

%clean
rm -rf $RPM_BUILD_ROOT

%post
echo 'configuring apache'

eval grep django /etc/httpd/conf/httpd.conf
# if apache not already configured for django, do it now
if [ $? -eq 1 ]; then
  SITEPKG=`python -c "from distutils.sysconfig import get_python_lib; print get_python_lib()"`
  cat  < /usr/django/resources/httpd-append.conf >> /etc/httpd/conf/httpd.conf 
  sed -i "s|PYTHON_SITEPKG|$SITEPKG|" /etc/httpd/conf/httpd.conf
fi

echo 'configure mysql'
# fix a bug in the mysql rpm installer that does not set correct permissions
chown -R mysql.mysql /var/lib/mysql
mysql_install_db

echo 'configuring cgit'
eval grep django /etc/cgitrc
if [ $? -eq 1 ]; then
  echo "include=/usr/django/cgitrepo" >>/etc/cgitrc
fi
if [ ! -f /usr/django/cgitrepo ]; then
  cp /usr/django/resources/cgitrepo /usr/django
  chmod 777 /usr/django/cgitrepo
fi

# stratosource configuration requirements

cp /usr/django/resources/my.cnf /etc
cp /usr/django/resources/memcached /etc/sysconfig
cp /usr/django/resources/django.wsgi /usr/django
mkdir /usr/django/logs
chmod 777 /usr/django/logs
mkdir /var/sfrepo
chmod 777 /var/sfrepo

echo 'restarting services'
service memcached restart
service httpd restart
service mysqld restart

%files
%defattr(-,root,root,-)
/usr/django/*

%changelog
* Fri Nov 5 2010 Mark Smith
  - Initial work on spec

