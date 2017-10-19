"""Analysis of model bias.

We look at differences in model scores as a way to compare bias in different
models.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import datetime
import os

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from model_tool import ToxModel, compute_auc

MODEL_DIR = '../models/'
ORIG_MADLIBS_PATH = '../eval_datasets/bias_madlibs_89k.csv'
SCORED_MADLIBS_PATH = '../eval_datasets/bias_madlibs_89k_scored.csv'
MADLIBS_TERMS_PATH = 'bias_madlibs_data/adjectives_people.txt'

### Model scoring

# Scoring these dataset for dozens of models actually takes non-trivial amounts
# of time, so we save the results as a CSV. The resulting CSV includes all the
# columns of the original dataset, and in addition has columns for each model,
# containing the model's scores.

def postprocess_madlibs(madlibs):
    """Modifies madlibs data to have standard 'text' and 'label' columns."""
    # Native madlibs data uses 'Label' column with values 'BAD' and 'NOT_BAD'.
    # Replace with a bool.
    madlibs['label'] = madlibs['Label'] == 'BAD'
    madlibs.drop('Label', axis=1, inplace=True)
    madlibs.rename(columns={'Text': 'text'}, inplace=True)


def score_dataset(df, models, text_col):
    """Scores the dataset with each model and adds the scores as new columns."""
    for model in models:
        name = model.get_model_name()
        print('{} Scoring with {}...'.format(datetime.datetime.now(), name))
        df[name] = model.predict(df[text_col])

def score_and_save_madlibs(models, scored_path=SCORED_MADLIBS_PATH,
                           orig_path=ORIG_MADLIBS_PATH):
    """Returns scored madlibs dataset. Saves scores."""
    madlibs = pd.read_csv(orig_path)
    postprocess_madlibs(madlibs)
    print('Scoring madlibs with all models.')
    score_dataset(madlibs, models, 'text')
    print('Saving scores to:', scored_path)
    madlibs.to_csv(scored_path)
    return madlibs

def load_scored_madlibs(models, scored_path=SCORED_MADLIBS_PATH,
                        orig_path=ORIG_MADLIBS_PATH):
    """Returns scored madlibs dataset. Tries to load previously-scored data."""
    if os.path.exists(scored_path):
        print('Using previously scored data:', scored_path)
        return pd.read_csv(scored_path)
    return score_and_save_madlibs(models, scored_path, orig_path)


### Per-term pinned AUC analysis.

def model_family_auc(dataset, model_names, label_col):
    aucs = [compute_auc(dataset[label_col], dataset[model_name])
            for model_name in model_names]
    return {
        'aucs': aucs,
        'mean': np.mean(aucs),
        'median': np.median(aucs),
        'std': np.std(aucs),
    }


def plot_model_family_auc(dataset, model_names, label_col, min_auc=0.9):
    result = model_family_auc(dataset, model_names, label_col)
    print('mean AUC:', result['mean'])
    print('median:', result['median'])
    print('stddev:', result['std'])
    plt.hist(result['aucs'])
    plt.gca().set_xlim([min_auc, 1.0])
    plt.show()
    return result


def read_madlibs_terms():
    with open(MADLIBS_TERMS_PATH) as f:
        return [term.strip() for term in f.readlines()]


def balanced_term_subset(df, term, text_col):
    """Returns data subset containing term balanced with sample of other data.

    We draw a random sample from the dataset of other examples because we don't
    care about the model's ability to distinguish toxic from non-toxic just
    within the term-specific dataset, but rather its ability to distinguish for
    the term-specific subset within the context of a larger distribution of
    data.
    """
    term_df = df[df[text_col].str.contains(r'\b{}\b'.format(term), case=False)]
    nonterm_df = df[~df.index.isin(term_df.index)].sample(len(term_df))
    combined = pd.concat([term_df, nonterm_df])
    return combined


def model_family_name(model_names):
    """Given a list of model names, returns the common prefix."""
    prefix = os.path.commonprefix(model_names)
    if not prefix:
        raise ValueError("couldn't determine family name from model names")
    return prefix.strip('_')


def per_term_aucs(dataset, terms, model_families, text_col, label_col):
    """Computes per-term 'pinned' AUC scores for each model family."""
    records = []
    for term in terms:
        term_subset = balanced_term_subset(dataset, term, text_col)
        term_record = {'term': term, 'subset_size': len(term_subset)}
        for model_family in model_families:
            family_name = model_family_name(model_family)
            aucs = [compute_auc(term_subset[label_col], term_subset[model_name])
                    for model_name in model_family]
            term_record.update({
                family_name + '_mean': np.mean(aucs),
                family_name + '_median': np.median(aucs),
                family_name + '_std': np.std(aucs),
                family_name + '_aucs': aucs,
            })
        records.append(term_record)
    return pd.DataFrame(records)


### Equality of opportunity negative rates analysis.

def confusion_matrix_counts(df, score_col, label_col, threshold):
    return {
        'tp': len(df[(df[score_col] >= threshold) & (df[label_col] == True)]),
        'tn': len(df[(df[score_col] < threshold) & (df[label_col] == False)]),
        'fp': len(df[(df[score_col] >= threshold) & (df[label_col] == False)]),
        'fn': len(df[(df[score_col] < threshold) & (df[label_col] == True)]),
    }


# https://en.wikipedia.org/wiki/Confusion_matrix
def compute_confusion_rates(df, score_col, label_col, threshold):
    confusion = confusion_matrix_counts(df, score_col, label_col, threshold)
    actual_positives = confusion['tp'] + confusion['fn']
    actual_negatives = confusion['tn'] + confusion['fp']
    # True positive rate, sensitivity, recall.
    tpr = confusion['tp'] / actual_positives
    # True negative rate, specificity.
    tnr = confusion['tn'] / actual_negatives
    # False positive rate, fall-out.
    fpr = 1 - tnr
    # False negative rate, miss rate.
    fnr = 1 - tpr
    # Precision, positive predictive value.
    precision = confusion['tp'] / (confusion['tp'] +  confusion['fp'])
    return {
        'tpr': tpr,
        'tnr': tnr,
        'fpr': fpr,
        'fnr': fnr,
        'precision': precision,
        'recall': tpr,
    }

def compute_equal_error_rate(df, score_col, label_col, num_thresholds=101):
    """Returns threshold where the false negative and false positive counts are equal."""
    # Note: I'm not sure if this should be based on the false positive/negative
    # *counts*, or the *rates*. However, they should be equivalent for balanced
    # datasets.
    thresholds = np.linspace(0, 1, num_thresholds)
    min_threshold = None
    min_confusion_matrix = None
    min_diff = float('inf')
    for threshold in thresholds:
        confusion_matrix = confusion_matrix_counts(df, score_col, label_col,
                                                   threshold)
        difference = abs(confusion_matrix['fn'] - confusion_matrix['fp'])
        if difference <= min_diff:
            min_diff = difference
            min_confusion_matrix = confusion_matrix
            min_threshold = threshold
        else:
            # min_diff should be monotonically non-decreasing, so once it
            # increases we can break. Yes, we could do a binary search instead.
            break
    return {
        'threshold': min_threshold,
        'confusion_matrix': min_confusion_matrix,
    }

def per_model_eer(dataset, label_col, model_names, num_eer_thresholds=101):
    """Computes the equal error rate for every model on the given dataset."""
    model_name_to_eer = {}
    for model_name in model_names:
        eer = compute_equal_error_rate(dataset, model_name, label_col,
                                       num_eer_thresholds)
        model_name_to_eer[model_name] = eer['threshold']
    return model_name_to_eer

def per_term_negative_rates(df, terms, model_families, threshold, text_col,
                            label_col):
    """Computes per-term true/false negative rates for all model families.

    Args:
      df: dataset to compute rates on.
      terms: negative rates are computed on subsets of the dataset containing
          each term.
      text_col: column in df containing the text.
      label_col: column in df containing the boolean label.
      model_families_names: list of model families; each model family is a list
          of model names in the family.
      threshold: threshold to use to compute negative rates. Can either be a
          float, or a dictionary mapping model name to float threshold in order
          to use a different threshold for each model.

    Returns:
      DataFrame with per-term false/true negative rates for each model family.
          Results are summarized across each model family, giving mean, median,
          and standard deviation of each negative rate.
    """
    records = []
    for term in terms:
        term_subset = df[df[text_col].str.contains(r'\b{}\b'.format(term),
                                                   case=False)]
        term_record = {'term': term, 'subset_size': len(term_subset)}
        for model_family in model_families:
            family_name = model_family_name(model_family)
            family_rates = []
            for model_name in model_family:
                model_threshold = (threshold[model_name]
                                   if isinstance(threshold, dict) else
                                   threshold)
                assert isinstance(model_threshold, float)
                model_rates = compute_confusion_rates(
                    term_subset, model_name, label_col, model_threshold)
                family_rates.append(model_rates)
            tnrs, fnrs = ([rates['tnr'] for rates in family_rates],
                          [rates['fnr'] for rates in family_rates])
            term_record.update({
                family_name + '_tnr_median': np.median(tnrs),
                family_name + '_tnr_mean': np.mean(tnrs),
                family_name + '_tnr_std': np.std(tnrs),
                family_name + '_tnr_values': tnrs,
                family_name + '_fnr_median': np.median(fnrs),
                family_name + '_fnr_mean': np.mean(fnrs),
                family_name + '_fnr_std': np.std(fnrs),
                family_name + '_fnr_values': fnrs,
            })
        records.append(term_record)
    return pd.DataFrame(records)
