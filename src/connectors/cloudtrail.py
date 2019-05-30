from runners.helpers import db, log
from runners.helpers.dbconfig import WAREHOUSE

FILE_FORMAT = """
    TYPE = "JSON",
    COMPRESSION = "AUTO",
    ENABLE_OCTAL = FALSE,
    ALLOW_DUPLICATE = FALSE,
    STRIP_OUTER_ARRAY = TRUE,
    STRIP_NULL_VALUES = FALSE,
    IGNORE_UTF8_ERRORS = FALSE,
    SKIP_BYTE_ORDER_MARK = TRUE
"""

CLOUDTRAIL_LANDING_TABLE = """
(raw VARIANT,
hash_raw NUMBER,
event_time TIMESTAMP_LTZ(9),
aws_region STRING,
event_id STRING,
event_name STRING,
event_source STRING,
event_type STRING,
event_version STRING,
recipient_account_id STRING,
request_id STRING,
request_parameters VARIANT,
response_elements VARIANT,
source_ip_address STRING,
user_agent STRING,
user_identity VARIANT,
user_identity_type STRING,
user_identity_principal_id STRING,
user_identity_arn STRING,
user_identity_accountid STRING,
user_identity_invokedby STRING,
user_identity_access_key_id STRING,
user_identity_username STRING,
user_identity_session_context_attributes_mfa_authenticated BOOLEAN,
user_identity_session_context_attributes_creation_date STRING,
user_identity_session_context_session_issuer_type STRING,
user_identity_session_context_session_issuer_principal_id STRING,
user_identity_session_context_session_issuer_arn STRING,
user_identity_session_context_session_issuer_account_id STRING,
user_identity_session_context_session_issuer_user_name STRING,
error_code STRING,
error_message STRING,
additional_event_data VARIANT,
api_version STRING,
read_only BOOLEAN,
resources VARIANT,
service_event_details STRING,
shared_event_id STRING,
vpc_endpoint_id STRING)
"""


def create_connector(data):
    # TODO: encapsulate the statements in a transaction

    name = f"{data['name']}_{data['type']}"
    bucket = data['options']['bucket']
    prefix = data['options']['prefix']
    role = data['options']['role']

    cloudtrail_ingest_task = f"""
INSERT INTO DATA.{name}_EVENTS (
  raw, hash_raw, event_time, aws_region, event_id, event_name, event_source, event_type, event_version,
  recipient_account_id, request_id, request_parameters, response_elements, source_ip_address,
  user_agent, user_identity, user_identity_type, user_identity_principal_id, user_identity_arn,
  user_identity_accountid, user_identity_invokedby, user_identity_access_key_id, user_identity_username,
  user_identity_session_context_attributes_mfa_authenticated, user_identity_session_context_attributes_creation_date,
  user_identity_session_context_session_issuer_type, user_identity_session_context_session_issuer_principal_id,
  user_identity_session_context_session_issuer_arn, user_identity_session_context_session_issuer_account_id,
  user_identity_session_context_session_issuer_user_name, error_code, error_message, additional_event_data,
  api_version, read_only, resources, service_event_details, shared_event_id, vpc_endpoint_id
)
SELECT value raw
    , HASH(value) hash_raw
    , value:eventTime::TIMESTAMP_LTZ(9) event_time
    , value:awsRegion::STRING aws_region
    , value:eventID::STRING event_id
    , value:eventName::STRING event_name
    , value:eventSource::STRING event_source
    , value:eventType::STRING event_type
    , value:eventVersion::STRING event_version
    , value:recipientAccountId::STRING recipient_account_id
    , value:requestID::STRING request_id
    , value:requestParameters::VARIANT request_parameters
    , value:responseElements::VARIANT response_elements
    , value:sourceIPAddress::STRING source_ip_address
    , value:userAgent::STRING user_agent
    , value:userIdentity::VARIANT user_identity
    , value:userIdentity.type::STRING user_identity_type
    , value:userIdentity.principalId::STRING user_identity_principal_id
    , value:userIdentity.arn::STRING user_identity_arn
    , value:userIdentity.accountId::STRING user_identity_accountid
    , value:userIdentity.invokedBy::STRING user_identity_invokedby
    , value:userIdentity.accessKeyId::STRING user_identity_access_key_id
    , value:userIdentity.userName::STRING user_identity_username
    , value:userIdentity.sessionContext.attributes.mfaAuthenticated::STRING user_identity_session_context_attributes_mfa_authenticated
    , value:userIdentity.sessionContext.attributes.creationDate::STRING user_identity_session_context_attributes_creation_date
    , value:userIdentity.sessionContext.sessionIssuer.type::STRING user_identity_session_context_session_issuer_type
    , value:userIdentity.sessionContext.sessionIssuer.principalId::STRING user_identity_session_context_session_issuer_principal_id
    , value:userIdentity.sessionContext.sessionIssuer.arn::STRING user_identity_session_context_session_issuer_arn
    , value:userIdentity.sessionContext.sessionIssuer.accountId::STRING user_identity_session_context_session_issuer_account_id
    , value:userIdentity.sessionContext.sessionIssuer.userName::STRING user_identity_session_context_session_issuer_user_name
    , value:errorCode::STRING error_code
    , value:errorMessage::STRING error_message
    , value:additionalEventData::VARIANT additional_event_data
    , value:apiVersion::STRING api_version
    , value:readOnly::BOOLEAN read_only
    , value:resources::VARIANT resources
    , value:serviceEventDetails::STRING service_event_details
    , value:sharedEventID::STRING shared_event_id
    , value:vpcEndpointID::STRING vpc_endpoint_id
FROM DATA.{name}_STREAM, table(flatten(input => v:Records))
WHERE ARRAY_SIZE(v:Records) > 0
"""

    comment = f"""
---
name: {data['name']}
type: {data['type']}
stale: {data['stale']}
method: pipe
target: {name}_PIPE
"""

    results = []

    try:
        db.create_stage(name=name+'_STAGE', url=bucket, prefix=prefix, cloud='aws',
                        credentials=role, file_format=FILE_FORMAT)
        results.append({'stage': 'success'})
    except Exception as e:
        return e

    try:
        db.create_table(name=name+'_STAGING', cols={'v': 'variant'})
        results.append({'staging_table': 'success'})
    except Exception as e:
        return e

    try:
        db.create_stream(name=name+'_STREAM', target=name+'_STAGING')
        results.append({'stream': 'success'})
    except Exception as e:
        return e

    try:
        pipe_sql = f"COPY INTO DATA.{name}_STAGING FROM @DATA.{name}_STAGE/"
        db.create_pipe(name=name+'_PIPE', sql=pipe_sql, replace=True)
        results.append({'pipe': 'success'})
    except Exception as e:
        return e

    try:
        db.create_table(name=name+"_EVENTS_CONNECTION", cols=CLOUDTRAIL_LANDING_TABLE, comment=comment)
        results.append({'events_table': 'success'})
    except Exception as e:
        return e

    try:
        db.create_task(name=name+'_TASK', schedule='15 minutes',
                       warehouse=WAREHOUSE, sql=cloudtrail_ingest_task)
        results.append({'task': 'success'})
    except Exception as e:
        return e

    data = db.execute(f'DESC STAGE DATA.{name}_STAGE')
    desc = list(data)
    for i in desc:
        if i[0] is 'STAGE_CREDENTIALS':
            results.append(i)

    return


def delete_connector(name, force=False):
    db.execute(f'DROP STAGE DATA.{name}_STAGE')
    db.execute(f'DROP STREAM DATA.{name}_STREAM')
    db.execute(f'DROP PIPE DATA.{name}_PIPE')
    db.execute(f'DROP TASK DATA.{name}_TASK')

    if force:
        db.execute(f'DROP TABLE DATA.{name}_STAGING')
        db.execute(f'DROP TABLE DATA.{name}_EVENTS_CONNECTION')

    return {'deleted': 'success', 'force': force}


def main():
    data = {'type': 'cloudtrail',
            'name': 'snowflake',
            'stale': '1 day',
            'options':
            {'bucket': 's3://tktk_test_bucket',
             'prefix': 'foo',
             'role': 'arn:aws:iam::0123412341:role/test_role'
             }}

    create_connector(data)


if __name__ == '__main__':
    main()
