#!/bin/bash

output_path=$(realpath ./)
mkdir -p ${output_path}

pushd $(dirname $0)/../ycsb-0.17.0 > /dev/null

echo "Initializing MySQL..."
if systemctl is-active --quiet mysql; then
    systemctl stop mysql
fi
sudo rm -rf /var/lib/mysql/
sudo mkdir -p /var/lib/mysql/
sudo chown -R mysql:mysql /var/lib/mysql/
sudo mysqld --initialize-insecure
if [ $? -ne 0 ]; then
    echo "Failed to initialize MySQL."
    exit 1
fi

echo "Start MySQL service..."
sudo systemctl start mysql
if systemctl is-active --quiet mysql; then
    echo "Successfully started MySQL service."
else
    echo "Failed to start MySQL service."
    exit 1
fi

workload_property_flags="
    -p recordcount=1000000
    -p fieldcount=16
    -p fieldlength=64
    -p minfieldlength=16

    -p operationcount=1000000

    -p readallfields=true
    -p writeallfields=false
"

USER_NAME="ycsb_user"
USER_DB_NAME="ycsb_db"
USER_PASSWORD="1111"

echo "Start creating MySQL user and database for ycsb test"
mysql -u root <<EOF
CREATE DATABASE IF NOT EXISTS ${USER_DB_NAME};
CREATE USER '${USER_NAME}'@'localhost' IDENTIFIED BY '${USER_PASSWORD}';
GRANT ALL PRIVILEGES ON ${USER_DB_NAME}.* TO '${USER_NAME}'@'localhost';
FLUSH PRIVILEGES;

USE ${USER_DB_NAME};
CREATE TABLE IF NOT EXISTS usertable(YCSB_KEY VARCHAR (255) PRIMARY KEY,     FIELD0 TEXT, FIELD1 TEXT,     FIELD2 TEXT, FIELD3 TEXT,     FIELD4 TEXT, FIELD5 TEXT,     FIELD6 TEXT, FIELD7 TEXT,     FIELD8 TEXT, FIELD9 TEXT, FIELD10 TEXT, FIELD11 TEXT, FIELD12 TEXT, FIELD13 TEXT, FIELD14 TEXT, FIELD15 TEXT);
EOF
echo "Successfully created users and dbs for ycsb test"

echo "Loading ycsb workload..." | tee ${output_path}/load_ycsb.log
./bin/ycsb load jdbc \
    -P workloads/workloada \
    -P jdbc-binding/conf/db.properties \
    -p db.driver=com.mysql.jdbc.Driver \
    -p db.url=jdbc:mysql://localhost:3306/${USER_DB_NAME} \
    -p db.user=${USER_NAME} \
    -p db.passwd=${USER_PASSWORD} \
    ${workload_property_flags} \
    -threads 72 \
    -s \
    2>&1 | tee ${output_path}/load_ycsb.log


popd > /dev/null

sudo systemctl stop mysql