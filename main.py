import re, numpy as np
from parameters import *

from keras import backend as K
from keras.models import Sequential
from keras.callbacks import ModelCheckpoint, LearningRateScheduler, Callback
from keras.optimizers import RMSprop, SGD

from vgg16_keras import VGG_16
from lstm_keras import LSTMnet
from get_data import get_data
from process_predictions import process_predictions

###############################
# Loading synset dictionaries #
###############################

synset_raw = [l.strip() for l in open(synset_path).readlines()]
synset_wnet2id = {}
synset_wnet2name = {}
synset_id2name = {}
regexp = "(n[0-9]*) ([0-9]*) ([a-z]*)"
for s in synset_raw:
    synset_wnet2id[re.match(regexp,s).group(1)] = re.match(regexp,s).group(2)
    synset_wnet2name[re.match(regexp,s).group(1)] = re.match(regexp,s).group(3)
    synset_id2name[re.match(regexp,s).group(2)] = re.match(regexp,s).group(3)
print 'Synset dictionaries loaded'

#############################
# Parameters of the network #
#############################

# Parameters
print 'Number of categories :', num_categories
print 'Side :', S

# LSTM parameters
nb_lstm_layer = 1
input_size = 512*7*7
hidden_size = [2048]
time_distributed = True
nb_frame = 16
dropout = 0
batchnorm = True

# Fitting parameters
batch_size = 1
validation_batch_size = 0
buckets_id = ['0000', '0001', '0002', '0003']
nb_samples = batch_size * 100
nb_epoch = 150
data_augmentation = True

print 'Number of frames per videos', nb_frame
print 'Batch size',batch_size
print 'Data augmentation', data_augmentation
print 'Batch normalization', batchnorm

#####################################
# Creating the custom loss function #
#####################################

def custom_loss(y_true, y_pred):
    # y_true and y_pred are
    # (nb_frame, S * S * (x,y,w,h,p1, p2, ..., p30, objectness))
    nb_features = 4 + num_categories + 1
    loss = 0.0
    lambda_coord = 5.0
    lambda_noobj = 0.5

    # # Loss with a for loop
    # y1 = y_pred
    # y2 = y_true
    #
    # for i in range(S*S): # Try to vectorize all the code
    #     y1_probs = y1[:,:, i*nb_features+4:((i+1)*nb_features - 1)]
    #     y2_probs = y2[:,:, i*nb_features+4:((i+1)*nb_features - 1)]
    #
    #     y1_coords = y1[:,:, i*nb_features:i*nb_features + 4]
    #     y2_coords = y2[:,:, i*nb_features:i*nb_features + 4]
    #
    #     noobj = ((y2[:,:, ((i+1)*35 - 1)] == 0))
    #
    #     # Only penalizing classification if an object is present
    #     loss_probs = K.sum(K.square(y1_probs - y2_probs),axis=2) * y2[:,:, ((i+1)*nb_features - 1)]
    #     # SSE weighted by lambda_coord, increasing localization loss
    #     loss_coords = K.sum(K.square(y1_coords - y2_coords),axis=2)
    #     # SSE weighted by lambda_noobj if no object is present
    #     lambda_conf = lambda_noobj * noobj + (1-noobj)
    #     loss_conf = K.square(y1[:,:, ((i+1)*nb_features - 1)] - y2[:,:, ((i+1)*nb_features - 1)])
    #
    #     loss = loss + K.sum(loss_probs + lambda_coord * loss_coords + lambda_conf * loss_conf, axis = 1)
    #
    # loss = K.sum(loss)

    # Loss without a for loop, much more efficient
    y1 = K.reshape(y_pred, (y_pred.shape[0], nb_frame, S, S, nb_features))
    y2 = K.reshape(y_true, (y_true.shape[0], nb_frame, S, S, nb_features))

    #y1_probs = y1[:,:,:,:,4:(nb_features - 1)]
    #y2_probs = y2[:,:,:,:,4:(nb_features - 1)]

    y1_coords = y1[:,:,:,:,0:4]
    y2_coords = y2[:,:,:,:,0:4]

    noobj = ((y2[:,:,:,:,(nb_features - 1)] == 0))

    # Only penalizing classification if an object is present
    #loss_probs = K.sum(K.square(y1_probs - y2_probs),axis=4) * y2[:,:,:,:,(nb_features - 1)]

    # SSE weighted by lambda_coord, increasing localization loss
    loss_coords = K.sum(K.square(y1_coords - y2_coords),axis=4) * (1 - noobj)
    # SSE weighted by lambda_noobj if no object is present
    lambda_conf = lambda_noobj * noobj + (1-noobj)
    loss_conf = K.square(y1[:,:,:,:,(nb_features - 1)] - y2[:,:,:,:,(nb_features - 1)])

    #loss = K.sum(K.flatten(loss_probs + lambda_coord * loss_coords + lambda_conf * loss_conf)) / batch_size
    loss = K.sum(K.flatten(lambda_coord * loss_coords + lambda_conf * loss_conf)) / y_pred.shape[0]

    return loss

#############################
# Loading the VGG16 network #
#############################

vgg16_network = VGG_16(vgg16_weights_path)
vgg16_optimizer = SGD(lr=0.1, decay=1e-6, momentum=0.9, nesterov=True)
vgg16_network.compile(optimizer=vgg16_optimizer, loss='categorical_crossentropy')

############################
# Loading the LSTM network #
############################

w_path = None
# w_path = lstm_weights_directory + 'checkpoint-loc-bn.hdf5'

lstm_network = LSTMnet(nb_lstm_layer, nb_frame, input_size, (4 + num_categories + 1) * S * S, hidden_size,
    time_distributed, w_path, dropout, batchnorm)
lstm_optimizer = RMSprop(lr=0.0001, rho=0.9, epsilon=1e-08)
lstm_network.compile(loss=custom_loss, optimizer=lstm_optimizer)

##############################
# Creating a batch generator #
##############################

def batchGenerator():
    b_id = 0
    while 1:
        (X_train, y_train, _, _, _) = get_data(image_net_directory, batch_size, nb_frame, 'train', synset_wnet2id,
            bucket_id = buckets_id[b_id], data_augmentation = data_augmentation)
        X_train = np.reshape(X_train, (-1, 3, 224, 224))
        X_train = vgg16_network.predict(X_train)
        X_train = np.reshape(X_train, (batch_size, nb_frame, 512 * 7 * 7))
        y_train = np.reshape(y_train, (batch_size, nb_frame, S * S * (4 + num_categories + 1)))
        b_id = (b_id + 1) % 4

        yield (X_train, y_train)

batchGenerator = batchGenerator()

print 'Batch generator loaded'

#########################################
# Creating a validation batch generator #
#########################################

def validationBatchGenerator():
    while 1:
        (X_val, y_val, _, _, _) = get_data(image_net_directory, validation_batch_size, nb_frame, 'val', synset_wnet2id)
        X_val = np.reshape(X_val, (-1, 3, 224, 224))
        X_val = vgg16_network.predict(X_val)
        X_val = np.reshape(X_val, (validation_batch_size, nb_frame, 512 * 7 * 7))
        y_val = np.reshape(y_val, (validation_batch_size, nb_frame, S * S * (4 + num_categories + 1)))

        yield (X_val, y_val)

validationBatchGenerator = validationBatchGenerator()

print 'Validation batch generator loaded'

##############################
# Initializing the callbacks #
##############################

def scheduler(epoch):
    if epoch >= 120:
        return float(0.000001)
    elif epoch >= 60:
        return float(0.00001)
    elif epoch >= 20:
        return float(0.0001)
    else:
        #return float(0.001 * (1 + epoch*0.5))
        return float(0.001)

class LearningRatePrinter(Callback):
    def init(self):
        super(LearningRatePrinter, self).init()

    def on_epoch_begin(self, epoch, logs={}):
        print 'lr:', self.model.optimizer.lr.get_value()

class HistoryCheckpoint(Callback):
    def on_train_begin(self, logs={}):
        self.epoch = 1
        f = open(lstm_weights_directory + 'checkpoint-history.txt', 'w')
        f.write('BatchSize\t'+batch_size+'\tDropout\t'+dropout+'\n')
        f.write('Epoch\tLoss\tlr\n')
        f.close()

    def on_epoch_end(self, batch, logs={}):
        f = open(lstm_weights_directory + 'checkpoint-history.txt', 'a')
        f.write(str(self.epoch) +'\t'+ str(logs.get('loss')) +'\t' + str(self.model.optimizer.lr.get_value()) +'\n')
        f.close()
        self.epoch += 1

lr_printer = LearningRatePrinter()

lr_scheduler = LearningRateScheduler(scheduler)

hist_checkpoint = HistoryCheckpoint()

checkpoint = ModelCheckpoint(filepath=lstm_weights_directory + 'checkpoint-loc-bn.hdf5')#, save_weights_only = True)

callbacks = [checkpoint, lr_printer, hist_checkpoint]

#####################
# Fitting the model #
#####################

train,test = False, True

if train:
    print 'Initializing the model...'
    lstm_network.fit_generator(batchGenerator, samples_per_epoch = nb_samples, nb_epoch = nb_epoch,
        verbose = 1, callbacks = callbacks)

###########################################
# Testing the model and show some results #
###########################################

if test:
    for i in range(5):
        (X_train, y_train, image_paths, label_paths, indexes) = get_data(image_net_directory, 1, nb_frame, 'val', synset_wnet2id,
            bucket_id = '0000', verbose = False, data_augmentation = False)
        print 'Processing video', image_paths[0], 'from frame', indexes[0]
        X_train = np.reshape(X_train, (-1, 3, 224, 224))
        X_train = vgg16_network.predict(X_train)
        X_train = np.reshape(X_train, (1, nb_frame, 512 * 7 * 7))
        y_pred = lstm_network.predict(X_train)
        y_train = np.reshape(y_train, (1, nb_frame, -1))

        process_predictions(y_pred, y_train, image_paths[0], label_paths[0], indexes[0], nb_frame, synset_id2name, show = True)
        print 'Computing loss...'
        print 'Loss',custom_loss(y_train, y_pred).eval()
