from arcgis.features import GeoAccessor, FeatureLayer
from arcgis.geometry import Geometry
from arcgis.gis import GIS
import numpy as np
import pandas as pd
import os
import re


def clean_columns(column_list):
    """
    Little helper to clean up column names quickly.
    :param column_list: List of column names.
    :return: List of cleaned up column names.
    """
    def _scrub_col(column):
        no_spc_char = re.sub(r'[^a-zA-Z0-9_\s]', '', column)
        no_spaces = re.sub(r'\s', '_', no_spc_char)
        return re.sub(r'_+', '_', no_spaces)
    return [_scrub_col(col) for col in column_list]


def get_dataframe(in_features, gis=None):
    """
    Get a spatially enabled dataframe from the input features provided.
    :param in_features: Spatially Enabled Dataframe | String path to Feature Class | String url to Feature Service
        | String Web GIS Item ID
        Resource to be evaluated and converted to a Spatially Enabled Dataframe.
    :param gis: Optional GIS object instance for connecting to resources.
    """

    # if already a Spatially Enabled Dataframe, mostly just pass it straight through
    if isinstance(in_features, pd.DataFrame) and in_features.spatial.validate() is True:
        df = in_features

    # if a csv previously exported from a Spatially Enabled Dataframe, get it in
    elif isinstance(in_features, str) and os.path.exists(in_features) and in_features.endswith('.csv'):
        df = pd.read_csv(in_features)
        df['SHAPE'] = df['SHAPE'].apply(lambda geom: Geometry(eval(geom)))

        # this almost always is the index written to the csv, so taking care of this
        if df.columns[0] == 'Unnamed: 0':
            df = df.set_index('Unnamed: 0')
            del (df.index.name)

    # create a Spatially Enabled Dataframe from the direct url to the Feature Service
    elif isinstance(in_features, str) and in_features.startswith('http'):

        # submitted urls can be lacking a few essential pieces, so handle some contingencies with some regex matching
        regex = re.compile(r'((^https?://.*?)(/\d{1,3})?)\?')
        srch = regex.search(in_features)

        # if the layer index is included, still clean by dropping any possible trailing url parameters
        if srch.group(3):
            in_features = f'{srch.group(1)}'

        # ensure at least the first layer is being referenced if the index was forgotten
        else:
            in_features = f'{srch.group(2)}/0'

            # if the layer is unsecured, a gis is not needed, but we have to handle differently
        if gis is not None:
            df = FeatureLayer(in_features, gis).query(out_sr=4326, as_df=True)
        else:
            df = FeatureLayer(in_features).query(out_sr=4326, as_df=True)

    # create a Spatially Enabled Dataframe from a Web GIS Item ID
    elif isinstance(in_features, str) and len(in_features) == 32:

        # if publicly shared on ArcGIS Online this anonymous gis can be used to access the resource
        if gis is None:
            gis = GIS()
        itm = gis.content.get(in_features)
        df = itm.layers[0].query(out_sr=4326, as_df=True)

    # create a Spatially Enabled Dataframe from a local feature class
    elif isinstance(in_features, str):
        df = GeoAccessor.from_featureclass(in_features)

    # sometimes there is an issue with modified or sliced dataframes with the SHAPE column not being correctly
    #    recognized as a geometry column, so try to set it as the geometry...just in case
    elif isinstance(in_features, pd.DataFrame) and 'SHAPE' in in_features.columns:
        in_features.spatial.set_geometry('SHAPE')
        df = in_features

        if df.spatial.validate() is False:
            raise Exception('Could not process input features for get_dataframe function. Although the input_features '
                            'appear to be in a Pandas Dataframe, the SHAPE column appears to not contain valid '
                            'geometries. The Dataframe is not validating using the *.spatial.validate function.')

    else:
        raise Exception('Could not process input features for get_dataframe function.')

    # ensure the universal spatial column is correctly being recognized
    df.spatial.set_geometry('SHAPE')

    return df


def add_metric_by_origin_dest(parent_df, join_df, join_metric_fld, fill_na_value=None):
    """
    Add a field to an already exploded origin to multiple destination table. The table must follow the standardized
        schema, which it will if created using the proximity functions in this package.
    :param parent_df: Parent destination dataframe the metric will be added onto.
    :param join_df: Dataframe containing matching origin id's, destination id's, and the metric to be added.
    :param join_metric_fld: The column containing the metric to be added.
    :param fill_na_value: Optional - String or integer to fill null values with. If not used, null values will not be
        filled.
    :return: Dataframe with the data added onto the original origin to multiple destination table.
    """
    # ensure everything is matching field types so the joins will work
    origin_dtype = parent_df['origin_id'].dtype
    dest_dtype = parent_df['destination_id_01'].dtype
    join_df['origin_id'] = join_df['origin_id'].astype(origin_dtype)
    join_df['destination_id'] = join_df['destination_id'].astype(dest_dtype)

    # for the table being joined to the parent, set a multi-index for the join
    join_df_idx = join_df.set_index(['origin_id', 'destination_id'])

    # get the number of destinations being used
    dest_fld_lst = [col for col in parent_df.columns if col.startswith('destination_id_')]

    # initialize the dataframe to iteratively receive all the data
    combined_df = parent_df

    # for every destination
    for dest_fld in dest_fld_lst:
        # create a label field with the label name with the destination id
        out_metric_fld = f'{join_metric_fld}{dest_fld[-3:]}'

        # join the label field onto the parent dataframe
        combined_df = combined_df.join(join_df_idx[join_metric_fld], on=['origin_id', dest_fld])

        # rename the label column using the named label column with the destination id
        combined_df.columns = [out_metric_fld if col == join_metric_fld else col for col in combined_df.columns]

        # if filling the null values, do it
        if fill_na_value is not None:
            combined_df[out_metric_fld].fillna(fill_na_value, inplace=True)

    return combined_df


def add_metric_by_dest(parent_df, join_df, join_id_fld, join_metric_fld, get_dummies=False, fill_na_value=None):
    """
    Add a field to an already exploded origin to multiple destination table. The table must follow the standardized
        schema, which it will if created using the proximity functions in this package.
    :param parent_df: Parent destination dataframe the metric will be added onto.
    :param join_df: Dataframe containing matching destination_id's, and the metric to be added.
    :param join_id_fld: Field to use for joining to the origin_id in the parent_df
    :param join_metric_fld: The column containing the metric to be added.
    :param get_dummies: Optional - Boolean indicating if make dummies should be run to explode out categorical values.
    :param fill_na_value: Optional - String or integer to fill null values with. If not used, null values will not be
        filled.
    :return: Dataframe with the data added onto the original origin to multiple destination table.
    """
    # ensure everything is matching field types so the joins will work
    if parent_df['origin_id'].dtype == 'O':
        convert_dtype = str
    else:
        convert_dtype = parent_df['origin_id'].dtype
    join_df[join_id_fld] = join_df[join_id_fld].astype(convert_dtype)

    # for the table being joined to the parent set the index for the join
    join_df_idx = join_df.set_index(join_id_fld)

    # get the number of destinations being used
    dest_fld_lst = [col for col in parent_df.columns if col.startswith('destination_id_')]

    # initialize the dataframe to iteratively receive all the data
    combined_df = parent_df

    # for every destination
    for dest_fld in dest_fld_lst:

        # create a label field with the label name with the destination id
        out_metric_fld = f'{join_metric_fld}{dest_fld[-3:]}'

        # join the label field onto the parent dataframe
        combined_df = combined_df.join(join_df_idx[join_metric_fld], on=dest_fld)

        # rename the label column using the named label column with the destination id
        combined_df.columns = [out_metric_fld if col == join_metric_fld else col for col in combined_df.columns]

        # if filling the null values, do it
        if fill_na_value is not None:
            combined_df[out_metric_fld].fillna(fill_na_value, inplace=True)

        # if get dummies...well, do it dummy!
        if get_dummies:
            combined_df = pd.get_dummies(combined_df, columns=[out_metric_fld])

    # if dummies were created, clean up column names
    if get_dummies:
        combined_df.columns = clean_columns(combined_df.columns)

    return combined_df


def add_normalized_columns_to_closest_dataframe(closest_df, closest_factor_fld_root, normalize_df, normalize_id_fld,
                                                normalize_fld, output_normalize_field_name, fill_na=None,
                                                drop_original_columns=False):
    """
    Normalize metrics in a dataframe by a demographic value for each geography - typically either total households or
        total population
    :param closest_df: Dataframe formatted from closest analysis with multiple destination locations.
    :param closest_factor_fld_root: The field room pattern to be normalized - the part of the name prefixing the _01
        numbering scheme.
    :param normalize_df: The dataframe containing the data to be used in normalizing the metric.
    :param normalize_id_fld: The field in the dataframe with a geographic identifier able to be used to join the data
        together.
    :param normalize_fld: The field with values to be used as the denominator when normalizing the data.
    :param output_normalize_field_name: Field name to be used for the normalized output fields.
    :param fill_na: Optional - If the normalized fields are null, the value to fill in.
    :param drop_original_columns: Boolean - whether or not to drop the original columns.
    :return:
    """
    # get the data type the normalize join field needs to be
    if closest_df['origin_id'].dtype == 'O':
        convert_dtype = str
    else:
        convert_dtype = closest_df['origin_id'].dtype

    # convert the normalize join field to this data type, make this the index, and extract this single series out
    normalize_df[normalize_id_fld] = normalize_df[normalize_id_fld].astype(convert_dtype)
    normalize_df = normalize_df.set_index(normalize_id_fld)
    normalize_srs = normalize_df[normalize_fld]

    # join this series to the closest dataframe
    normalize_df = closest_df.join(normalize_srs, on='origin_id')

    # get a list of the fields we are going to normalize
    gross_factor_fld_lst = [col for col in normalize_df.columns if col.startswith(closest_factor_fld_root)]

    # for every field we are going to normalize, add a new normalized field
    for gross_fld in gross_factor_fld_lst:
        normalized_fld = gross_fld.replace(closest_factor_fld_root, output_normalize_field_name)
        normalize_df[normalized_fld] = normalize_df[gross_fld] / normalize_df[normalize_fld]

        # if a fill null value is provided, use it
        if fill_na is not None:
            normalize_df[normalized_fld].fillna(fill_na, inplace=True)

        # if the numerator is a value and the denominator is zero, the product is inf; we need zero
        normalize_df[normalized_fld] = normalize_df[normalized_fld].apply(
            lambda val: 0 if val == np.inf or val == -np.inf else val)

    # if we want to drop the columns, get rid of them
    if drop_original_columns:
        normalize_df.drop(columns=gross_factor_fld_lst, inplace=True)

    # we do not need the values we normalized by, so drop them
    normalize_df.drop(columns=normalize_fld, inplace=True)

    return normalize_df
