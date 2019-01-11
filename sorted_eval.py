# # Implementing CLSM

# ## Purpose
# The purpose of this notebook is to implement Microsoft's [Convolutional Latent Semantic Model](http://www.iro.umontreal.ca/~lisa/pointeurs/ir0895-he-2.pdf) on our dataset.
# 
# ## Inputs
# - This notebook requires *wiki-pages* from the FEVER dataset as an input.

# ## Preprocessing Data

import pickle
from multiprocessing import cpu_count
import os
from parallel import DataParallelModel, DataParallelCriterion
import parallel

import joblib
import nltk
import time
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import torchvision.datasets as dsets
import torchvision.transforms as transforms
from joblib import Parallel, delayed
from logger import Logger
from hyperdash import Experiment, monitor
from scipy import sparse
from sys import argv
from sklearn.preprocessing import LabelEncoder, OneHotEncoder
from sklearn.metrics import classification_report, accuracy_score
from torch.autograd import Variable
from torch.utils.data import DataLoader
from tqdm import tqdm, tqdm_notebook

import cdssm
import pytorch_data_loader
import argparse
import utils

torch.backends.cudnn.benchmark=True
nltk.data.path.append('/usr/users/mnadeem/nltk_data/')

def parse_args():
    parser = argparse.ArgumentParser(description='Learning the optimal convolution for network.')
    parser.add_argument("--batch-size", type=int, help="Number of queries per batch.", default=1)
    parser.add_argument("--data-batch-size", type=int, help="Number of examples per query.", default=8)
    parser.add_argument("--learning-rate", type=float, help="Learning rate for model.", default=1e-3)
    parser.add_argument("--epochs", type=int, help="Number of epochs to learn for.", default=3)
    parser.add_argument("--data", help="Training dataset to load file from.", default="shared_task_dev.pkl")
    parser.add_argument("--model", help="Model to evaluate.") 
    parser.add_argument("--sparse-evidences", default=False, action="store_true")
    return parser.parse_args()

@monitor("CLSM Test")
def run():
    BATCH_SIZE = args.batch_size
    LEARNING_RATE = args.learning_rate
    DATA_BATCH_SIZE = args.data_batch_size
    NUM_EPOCHS = args.epochs
    MODEL = args.model

    use_cuda = torch.cuda.is_available()
    device = torch.device("cuda:0" if use_cuda else "cpu")

    logger = Logger('./logs/{}'.format(time.localtime()))

    print("Created model...")
    model = cdssm.CDSSM()
    model = model.cuda()
    model = model.to(device)
    if torch.cuda.device_count() > 0:
      print("Let's use", torch.cuda.device_count(), "GPU(s)!")
      model = nn.DataParallel(model)
    model.load_state_dict(torch.load(MODEL))

    print("Created dataset...")
    dataset = pytorch_data_loader.WikiDataset(test, claims_dict, data_batch_size=DATA_BATCH_SIZE, testFile="shared_task_dev.jsonl") 
    dataloader = DataLoader(dataset, batch_size=BATCH_SIZE, num_workers=0, shuffle=True, collate_fn=pytorch_data_loader.PadCollate())

    OUTPUT_FREQ = int((len(dataset)/BATCH_SIZE)*0.02) 
    
    parameters = {"batch size": BATCH_SIZE, "data batch size": DATA_BATCH_SIZE, "data": args.data}
    exp_params = {}
    exp = Experiment("CLSM V2")
    for key, value in parameters.items():
       exp_params[key] = exp.param(key, value) 

    true = []
    pred = []
    print("Evaluating...")
    model.eval()
    test_running_accuracy = 0.0
    test_running_recall_at_ten = 0.0

    recall_intervals = [1,2,5,10,40,100,200,400]
    recall = {}
    for i in recall_intervals:
        recall[i] = []

    num_batches = 0

    for batch_num, inputs in enumerate(dataloader):
        num_batches += 1
        claims_tensors, claims_text, evidences_tensors, evidences_text, labels = inputs  

        #claims_tensors = claims_tensors.cuda()
        #evidences_tensors = evidences_tensors.cuda()
        #labels = labels.cuda()

        try:
            y_pred = model(claims_tensors, evidences_tensors)
        except:
            print(claims_text, evidences_text)

        y = (labels).float()

        y_pred = y_pred.squeeze()
        y = y.squeeze()

        # flatten tensors
        y = y.view(-1)
        y_pred = y_pred.view(-1)

        bin_acc = torch.sigmoid(y_pred).cuda()
        sorted_idxs = torch.sort(bin_acc, descending=True)[1]

        # for idx in range(len(y)):
          # print("Claim: {}, Evidence: {}, Prediction: {}, Label: {}".format(claims_text[idx], evidences_text[idx], bin_acc[idx], y[idx])) 
        
        # compute recall
        # assuming only one claim, this creates a list of all relevant evidences

        relevant_evidences = []
        for idx in range(len(y)):
            if y[idx]:
                relevant_evidences.append(evidences_text[idx])

        if len(relevant_evidences)==0:
            print("Zero relevant", y.sum())

        retrieved_evidences = []
        for idx in sorted_idxs:
            retrieved_evidences.append(evidences_text[idx])

        for k in recall_intervals:
            # recall[k].append(calculate_recall(retrieved_evidences, relevant_evidences, k=k))
            recall[k].append(0)

        # test_running_recall_at_ten += calculate_recall(retrieved_evidences, relevant_evidences, k=20)
        test_running_recall_at_ten += 0.0

        y = y.round()
        bin_acc = bin_acc.round()
        true.extend(y.tolist())
        pred.extend(bin_acc.tolist())

        accuracy = (y==bin_acc)
        accuracy = accuracy.float().mean()
        test_running_accuracy += accuracy.item()

        if batch_num % OUTPUT_FREQ==0 and batch_num>0:
            print("[{}]: accuracy: {}, recall@20: {}".format(batch_num, test_running_accuracy / OUTPUT_FREQ, test_running_recall_at_ten / OUTPUT_FREQ))

            # 1. Log scalar values (scalar summary)
            info = { 'test_accuracy': test_running_accuracy/OUTPUT_FREQ }

            for tag, value in info.items():
                exp.metric(tag, value, log=False)
            #     logger.scalar_summary(tag, value, batch_num+1)

            # 2. Log values and gradients of the parameters (histogram summary)
            # for tag, value in model.named_parameters():
            #     tag = tag.replace('.', '/')
            #     logger.histo_summary(tag, value.data.cpu().numpy(), batch_num+1)

            test_running_accuracy = 0.0
            test_running_recall_at_ten = 0.0

        # del claims_tensors
        # del claims_text
        # del evidences_tensors
        # del evidences_text
        # del labels 
        # torch.cuda.empty_cache()

    final_accuracy = accuracy_score(true, pred)
    print("Final accuracy: {}".format(final_accuracy))
    true = [int(i) for i in true]
    pred = [int(i) for i in pred]
    print(classification_report(true, pred))

    for k, v in recall.items():
        print("Recall@{}: {}".format(k, np.mean(v)))

    filename = "predicted_labels/predicted_labels"
    for key, value in parameters.items():
        filename += "_{}-{}".format(key.replace(" ", "_"), value)

    joblib.dump({"true": true, "pred": pred}, filename)

def calculate_precision(retrieved, relevant, k=None):
    """
        retrieved: a list of sorted documents that were retrieved
        relevant: a list of sorted documents that are relevant
        k: how many documents to consider, all by default.
    """
    if k==None:
        k = len(retrieved)
    return len(set(retrieved[:k]).intersection(set(relevant))) / len(set(retrieved))

def calculate_recall(retrieved, relevant, k=None):
    """
        retrieved: a list of sorted documents that were retrieved
        relevant: a list of sorted documents that are relevant
        k: how many documents to consider, all by default.
    """
    if k==None:
        k = len(retrieved)
    return len(set(retrieved[:k]).intersection(set(relevant))) / len(set(relevant))

if __name__=="__main__":
    args = parse_args()

    print("Loading {}".format(args.data))
    test = joblib.load(args.data)

    try:
        claims_dict
    except:
        print("Loading validation claims data...")
        claims_dict = joblib.load("claims_dict.pkl")

    torch.multiprocessing.set_start_method("spawn", force=True)
    run()
