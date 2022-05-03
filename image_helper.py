from collections import defaultdict
from typing import OrderedDict
import matplotlib.pyplot as plt
from prometheus_client import Counter

import torch
import torch.utils.data

from helper import Helper
import random
import logging
from torchvision import datasets, transforms
import numpy as np

from models.resnet_cifar import ResNet18
from models.MnistNet import MnistNet
from models.resnet_tinyimagenet import resnet18
logger = logging.getLogger("logger")
import config
from config import device
import copy
import cv2

import yaml

import os
os.environ['KMP_DUPLICATE_LIB_OK']='True'
import datetime
import json

from tqdm import tqdm
from collections import Counter


class ImageHelper(Helper):

    def create_model(self):
        local_model=None
        target_model=None
        if self.params['type']==config.TYPE_CIFAR:
            local_model = ResNet18(name='Local',
                                   created_time=self.params['current_time'])
            target_model = ResNet18(name='Target',
                                   created_time=self.params['current_time'])

        elif self.params['type']==config.TYPE_MNIST:
            local_model = MnistNet(name='Local',
                                   created_time=self.params['current_time'])
            target_model = MnistNet(name='Target',
                                    created_time=self.params['current_time'])

        elif self.params['type']==config.TYPE_TINYIMAGENET:

            local_model= resnet18(name='Local',
                                   created_time=self.params['current_time'])
            target_model = resnet18(name='Target',
                                    created_time=self.params['current_time'])

        local_model=local_model.to(device)
        target_model=target_model.to(device)
        if self.params['resumed_model']:
            if torch.cuda.is_available() :
                loaded_params = torch.load(f"saved_models/{self.params['resumed_model_name']}")
            else:
                loaded_params = torch.load(f"saved_models/{self.params['resumed_model_name']}",map_location='cpu')
            target_model.load_state_dict(loaded_params['state_dict'])
            self.start_epoch = loaded_params['epoch']+1
            self.params['lr'] = loaded_params.get('lr', self.params['lr'])
            logger.info(f"Loaded parameters from saved model: LR is"
                        f" {self.params['lr']} and current epoch is {self.start_epoch}")
        else:
            self.start_epoch = 1

        self.local_model = local_model
        self.target_model = target_model

    def new_model(self):
        if self.params['type']==config.TYPE_CIFAR:
            new_model = ResNet18(name='Dummy',
                                   created_time=self.params['current_time'])

        elif self.params['type']==config.TYPE_MNIST:
            new_model = MnistNet(name='Dummy',
                                    created_time=self.params['current_time'])

        elif self.params['type']==config.TYPE_TINYIMAGENET:
            new_model = resnet18(name='Dummy',
                                    created_time=self.params['current_time'])

        new_model=new_model.to(device)
        return new_model        

    def build_classes_dict(self):
        cifar_classes = {}
        for ind, x in enumerate(self.train_dataset):  # for cifar: 50000; for tinyimagenet: 100000
            _, label = x
            if label in cifar_classes:
                cifar_classes[label].append(ind)
            else:
                cifar_classes[label] = [ind]
        return cifar_classes

    def sample_dirichlet_train_data(self, no_participants, alpha=0.9):
        """
            Input: Number of participants and alpha (param for distribution)
            Output: A list of indices denoting data in CIFAR training set.
            Requires: cifar_classes, a preprocessed class-indice dictionary.
            Sample Method: take a uniformly sampled 10-dimension vector as parameters for
            dirichlet distribution to sample number of images in each class.
        """

        cifar_classes = self.classes_dict
        class_size = len(cifar_classes[0]) #for cifar: 5000
        per_participant_list = defaultdict(list)
        no_classes = len(cifar_classes.keys())  # for cifar: 10

        image_nums = []
        for n in range(no_classes):
            image_num = []
            random.shuffle(cifar_classes[n])
            sampled_probabilities = class_size * np.random.dirichlet(
                np.array(no_participants * [alpha]))
            for user in range(no_participants):
                no_imgs = int(round(sampled_probabilities[user]))
                sampled_list = cifar_classes[n][:min(len(cifar_classes[n]), no_imgs)]
                image_num.append(len(sampled_list))
                per_participant_list[user].extend(sampled_list)
                cifar_classes[n] = cifar_classes[n][min(len(cifar_classes[n]), no_imgs):]
            image_nums.append(image_num)
        # self.draw_dirichlet_plot(no_classes,no_participants,image_nums,alpha)
        return per_participant_list

    def draw_dirichlet_plot(self,no_classes,no_participants,image_nums,alpha):
        fig= plt.figure(figsize=(10, 5))
        s = np.empty([no_classes, no_participants])
        for i in range(0, len(image_nums)):
            for j in range(0, len(image_nums[0])):
                s[i][j] = image_nums[i][j]
        s = s.transpose()
        left = 0
        y_labels = []
        category_colors = plt.get_cmap('RdYlGn')(
            np.linspace(0.15, 0.85, no_participants))
        for k in range(no_classes):
            y_labels.append('Label ' + str(k))
        vis_par=[0,10,20,30]
        for k in range(no_participants):
        # for k in vis_par:
            color = category_colors[k]
            plt.barh(y_labels, s[k], left=left, label=str(k), color=color)
            widths = s[k]
            xcenters = left + widths / 2
            r, g, b, _ = color
            text_color = 'white' if r * g * b < 0.5 else 'darkgrey'
            # for y, (x, c) in enumerate(zip(xcenters, widths)):
            #     plt.text(x, y, str(int(c)), ha='center', va='center',
            #              color=text_color,fontsize='small')
            left += s[k]
        plt.legend(ncol=20,loc='lower left',  bbox_to_anchor=(0, 1),fontsize=4) #
        # plt.legend(ncol=len(vis_par), bbox_to_anchor=(0, 1),
        #            loc='lower left', fontsize='small')
        plt.xlabel("Number of Images", fontsize=16)
        # plt.ylabel("Label 0 ~ 199", fontsize=16)
        # plt.yticks([])
        fig.tight_layout(pad=0.1)
        # plt.ylabel("Label",fontsize='small')
        fig.savefig(self.folder_path+'/Num_Img_Dirichlet_Alpha{}.pdf'.format(alpha))

    def poison_test_dataset(self):
        logger.info('get poison test loader')
        # delete the test data with target label
        test_classes = {}
        for ind, x in enumerate(self.test_dataset):
            _, label = x
            if label in test_classes:
                test_classes[label].append(ind)
            else:
                test_classes[label] = [ind]

        range_no_id = list(range(0, len(self.test_dataset)))
        for image_ind in test_classes[self.params['poison_label_swap']]:
            if image_ind in range_no_id:
                range_no_id.remove(image_ind)
        poison_label_inds = test_classes[self.params['poison_label_swap']]

        return torch.utils.data.DataLoader(self.test_dataset,
                           batch_size=self.params['batch_size'],
                           sampler=torch.utils.data.sampler.SubsetRandomSampler(
                               range_no_id)), \
               torch.utils.data.DataLoader(self.test_dataset,
                                            batch_size=self.params['batch_size'],
                                            sampler=torch.utils.data.sampler.SubsetRandomSampler(
                                                poison_label_inds))

    def get_label_skew_ratios(self, dataset, id, num_of_classes=10):
        dataset_classes = {}
        # for ind, x in enumerate(dataset):
        #     _, label = x
        #     #if ind in self.params['poison_images'] or ind in self.params['poison_images_test']:
        #     #    continue
        #     if label in dataset_classes:
        #         dataset_classes[label] += 1
        #     else:
        #         dataset_classes[label] = 1
        # for key in dataset_classes.keys():
        #     # dataset_classes[key] = dataset_classes[key] 

        #     dataset_classes[key] = float("{:.2f}".format(dataset_classes[key]/len(dataset)))
        if self.params['noniid']:
            y_labels = []
            for x, y in dataset:
                y_labels.append(y)
        else:
            y_labels=[t.item() for t in dataset.targets]
            indices = self.indices_per_participant[id]
            y_labels = np.array(y_labels)
            y_labels = y_labels[indices]
        dataset_dict = OrderedDict(Counter(y_labels))
        dataset_dict = OrderedDict(sorted(dataset_dict.items()))
        # for c in range(num_of_classes):
        #     dataset_classes.append(dataset_dict[c])
        # dataset_classes = np.array(dataset_classes)
        # print(dataset_classes)
        dataset_classes = np.array(list(dataset_dict.values()))
        dataset_classes = dataset_classes/np.sum(dataset_classes)
        return dataset_classes

    def assign_data(self, train_data, bias, num_labels=10, num_workers=100, server_pc=100, p=0.01, server_case2_cls=0, dataset="FashionMNIST", seed=1, flt_aggr=True):
        # assign data to the clients
        other_group_size = (1 - bias) / (num_labels - 1)
        worker_per_group = num_workers / num_labels

        #assign training data to each worker
        each_worker_data = [[] for _ in range(num_workers)]
        each_worker_label = [[] for _ in range(num_workers)]   
        server_data = []
        server_label = []
        
        # compute the labels needed for each class
        real_dis = [1. / num_labels for _ in range(num_labels)]
        samp_dis = [0 for _ in range(num_labels)]
        num1 = int(server_pc * p)
        samp_dis[server_case2_cls] = num1
        average_num = (server_pc - num1) / (num_labels - 1)
        resid = average_num - np.floor(average_num)
        sum_res = 0.
        for other_num in range(num_labels - 1):
            if other_num == server_case2_cls:
                continue
            samp_dis[other_num] = int(average_num)
            sum_res += resid
            if sum_res >= 1.0:
                samp_dis[other_num] += 1
                sum_res -= 1
        samp_dis[num_labels - 1] = server_pc - np.sum(samp_dis[:num_labels - 1])

        # privacy experiment only
        server_additional_label_0_samples_counter = 0    
        server_add_data=[]
        server_add_label=[]

        # randomly assign the data points based on the labels
        server_counter = [0 for _ in range(num_labels)]
        for _, (x, y) in enumerate(train_data):


            upper_bound = y * (1. - bias) / (num_labels - 1) + bias
            lower_bound = y * (1. - bias) / (num_labels - 1)

            upper_bound_offset = 0
            rd = np.random.random_sample()


            other_group_size = (1 - upper_bound - upper_bound_offset + lower_bound) / (num_labels - 1)

            if rd > upper_bound + upper_bound_offset:
                worker_group = int(np.floor((rd - upper_bound - upper_bound_offset) / other_group_size) + y + 1)
            elif rd < lower_bound:
                worker_group = int(np.floor(rd / other_group_size))
            # experiment 2 only
            elif rd > upper_bound:
                continue
            else:
                worker_group = y

            if server_counter[int(y)] < samp_dis[int(y)] and flt_aggr:
                server_data.append(x)
                server_label.append(y)
                server_counter[int(y)] += 1
            else:
                rd = np.random.random_sample()
                selected_worker = int(worker_group * worker_per_group + int(np.floor(rd * worker_per_group)))
                each_worker_data[selected_worker].append(x)
                each_worker_label[selected_worker].append(y)

        return server_data, server_label, each_worker_data, each_worker_label, server_add_data, server_add_label

    def load_data(self):
        logger.info('Loading data')
        dataPath = './data'
        if self.params['type'] == config.TYPE_CIFAR:
            ### data load
            transform_train = transforms.Compose([
                transforms.ToTensor(),
            ])

            transform_test = transforms.Compose([
                transforms.ToTensor(),
            ])

            self.train_dataset = datasets.CIFAR10(dataPath, train=True, download=True,
                                             transform=transform_train)

            self.test_dataset = datasets.CIFAR10(dataPath, train=False, transform=transform_test)

        elif self.params['type'] == config.TYPE_MNIST:

            self.train_dataset = datasets.MNIST('./data', train=True, download=True,
                               transform=transforms.Compose([
                                   transforms.ToTensor(),
                                   # transforms.Normalize((0.1307,), (0.3081,))
                               ]))
            self.test_dataset = datasets.MNIST('./data', train=False, transform=transforms.Compose([
                    transforms.ToTensor(),
                    # transforms.Normalize((0.1307,), (0.3081,))
                ]))
        elif self.params['type'] == config.TYPE_TINYIMAGENET:

            _data_transforms = {
                'train': transforms.Compose([
                    # transforms.Resize(224),
                    transforms.RandomHorizontalFlip(),
                    transforms.ToTensor(),
                ]),
                'val': transforms.Compose([
                    # transforms.Resize(224),
                    transforms.ToTensor(),
                ]),
            }
            _data_dir = './data/tiny-imagenet-200/'
            self.train_dataset = datasets.ImageFolder(os.path.join(_data_dir, 'train'),
                                                    _data_transforms['train'])
            self.test_dataset = datasets.ImageFolder(os.path.join(_data_dir, 'val'),
                                                   _data_transforms['val'])
            logger.info('reading data done')

        target_class_test_data=[]
        for _, (x, y) in enumerate(self.test_dataset):
            if y==self.params['targeted_label_flip_class']:
                target_class_test_data.append((x, y))
        self.target_class_test_loader = torch.utils.data.DataLoader(target_class_test_data, batch_size=self.params['test_batch_size'], shuffle=True)

        self.classes_dict = self.build_classes_dict()
        logger.info('build_classes_dict done')
        if self.params['noniid']:
            sd, sl, ewd, ewl, sad, sal = self.assign_data(self.train_dataset, bias=0.1, p=0.1, flt_aggr=1)
            if self.params['aggregation_methods'] == config.AGGR_FLTRUST:
                ewd.append(sd)
                ewl.append(sl)

            train_loaders = []
            for id_worker in range(len(ewd)):
                dataset_per_worker=[]
                for idx in range(len(ewd[id_worker])):
                    dataset_per_worker.append((ewd[id_worker][idx], ewl[id_worker][idx]))
                if len(dataset_per_worker) != 0:
                    train_loader = torch.utils.data.DataLoader(dataset_per_worker, batch_size=self.params['batch_size'], shuffle=True)
                    train_loaders.append((id_worker, train_loader))
        elif self.params['sampling_dirichlet']:
            ## sample indices for participants using Dirichlet distribution
            indices_per_participant = self.sample_dirichlet_train_data(
                self.params['number_of_total_participants'], #100
                alpha=self.params['dirichlet_alpha'])
            self.indices_per_participant = indices_per_participant
            train_loaders = [(pos, self.get_train(indices)) for pos, indices in
                             indices_per_participant.items()]
        else:
            ## sample indices for participants that are equally
            all_range = list(range(len(self.train_dataset)))
            random.shuffle(all_range)
            train_loaders = [(pos, self.get_train_old(all_range, pos))
                             for pos in range(self.params['number_of_total_participants'])]

        logger.info('train loaders done')
        self.train_data = train_loaders

        if self.params['noniid'] or self.params['sampling_dirichlet']:
            self.lsrs = []

            for id in tqdm(range(len(train_loaders))):
                (_, train_loader) = train_loaders[id]
                lsr = self.get_label_skew_ratios(train_loader.dataset, id)
                self.lsrs.append(lsr)

            logger.info(f'lsrs ready: {self.lsrs}')

        self.test_data = self.get_test()
        self.test_data_poison ,self.test_targetlabel_data = self.poison_test_dataset()


        if self.params['is_random_namelist'] == False:
            self.participants_list = self.params['participants_namelist']
        else:
            self.participants_list = list(range(self.params['number_of_total_participants']))
        # random.shuffle(self.participants_list)

        self.poison_epochs_by_adversary = {}
        if self.params['random_adversary_for_label_flip']:
            self.adversarial_namelist = random.sample(self.participants_list, self.params['number_of_adversary_for_label_flip'])
            for idx, id in enumerate(self.adversarial_namelist):
                self.poison_epochs_by_adversary[idx] = self.params[f'0_poison_epochs']
        else:
            self.adversarial_namelist = self.params['adversary_list']
            for idx, id in enumerate(self.adversarial_namelist):
                self.poison_epochs_by_adversary[idx] = self.params[f'{idx}_poison_epochs']

        self.benign_namelist =list(set(self.participants_list) - set(self.adversarial_namelist))

    def get_train(self, indices):
        """
        This method is used along with Dirichlet distribution
        :param params:
        :param indices:
        :return:
        """
        train_loader = torch.utils.data.DataLoader(self.train_dataset,
                                           batch_size=self.params['batch_size'],
                                           sampler=torch.utils.data.sampler.SubsetRandomSampler(
                                               indices),pin_memory=True, num_workers=8)
        return train_loader

    def get_train_old(self, all_range, model_no):
        """
        This method equally splits the dataset.
        :param params:
        :param all_range:
        :param model_no:
        :return:
        """

        data_len = int(len(self.train_dataset) / self.params['number_of_total_participants'])
        sub_indices = all_range[model_no * data_len: (model_no + 1) * data_len]
        train_loader = torch.utils.data.DataLoader(self.train_dataset,
                                           batch_size=self.params['batch_size'],
                                           sampler=torch.utils.data.sampler.SubsetRandomSampler(
                                               sub_indices))
        return train_loader

    def get_test(self):
        test_loader = torch.utils.data.DataLoader(self.test_dataset,
                                                  batch_size=self.params['test_batch_size'],
                                                  shuffle=True)
        return test_loader


    def get_batch(self, train_data, bptt, evaluation=False):
        data, target = bptt
        data = data.to(device)
        target = target.to(device)
        if evaluation:
            data.requires_grad_(False)
            target.requires_grad_(False)
        return data, target

    def get_poison_batch_for_targeted_label_flip(self, bptt, target_class=-1):

        images, targets = bptt

        poison_count= 0
        new_images=images
        new_targets=targets

        if target_class==-1:
            target_class = self.params['targeted_label_flip_class']

        for index in range(0, len(images)):
            if targets[index]==target_class: # poison all data when testing
                new_targets[index] = 10-target_class-1
                new_images[index] = images[index]
                poison_count+=1
            else:
                new_images[index] = images[index]
                new_targets[index]= targets[index]
            # new_targets[index] = self.params['targeted_label_flip_class']
            # new_images[index] = images[index]
            # poison_count+=1

        new_images = new_images.to(device)
        new_targets = new_targets.to(device).long()
        return new_images,new_targets,poison_count    

    def get_poison_batch(self, bptt,adversarial_index=-1, evaluation=False):

        images, targets = bptt

        poison_count= 0
        new_images=images
        new_targets=targets

        for index in range(0, len(images)):
            if evaluation: # poison all data when testing
                new_targets[index] = self.params['poison_label_swap']
                new_images[index] = self.add_pixel_pattern(images[index],adversarial_index)
                poison_count+=1

            else: # poison part of data when training
                if index < self.params['poisoning_per_batch']:
                    new_targets[index] = self.params['poison_label_swap']
                    new_images[index] = self.add_pixel_pattern(images[index],adversarial_index)
                    poison_count += 1
                else:
                    new_images[index] = images[index]
                    new_targets[index]= targets[index]

        new_images = new_images.to(device)
        new_targets = new_targets.to(device).long()
        if evaluation:
            new_images.requires_grad_(False)
            new_targets.requires_grad_(False)
        return new_images,new_targets,poison_count

    def add_pixel_pattern(self,ori_image,adversarial_index):
        image = copy.deepcopy(ori_image)
        poison_patterns= []
        if adversarial_index==-1:
            for i in range(0,self.params['trigger_num']):
                poison_patterns = poison_patterns+ self.params[str(i) + '_poison_pattern']
        else :
            poison_patterns = self.params[str(adversarial_index) + '_poison_pattern']
        if self.params['type'] == config.TYPE_CIFAR or self.params['type'] == config.TYPE_TINYIMAGENET:
            for i in range(0,len(poison_patterns)):
                pos = poison_patterns[i]
                image[0][pos[0]][pos[1]] = 1
                image[1][pos[0]][pos[1]] = 1
                image[2][pos[0]][pos[1]] = 1


        elif self.params['type'] == config.TYPE_MNIST:

            for i in range(0, len(poison_patterns)):
                pos = poison_patterns[i]
                image[0][pos[0]][pos[1]] = 1

        return image

if __name__ == '__main__':
    np.random.seed(1)
    with open(f'./utils/cifar_params.yaml', 'r') as f:
        params_loaded = yaml.load(f)
    current_time = datetime.datetime.now().strftime('%b.%d_%H.%M.%S')
    helper = ImageHelper(current_time=current_time, params=params_loaded,
                        name=params_loaded.get('name', 'mnist'))
    helper.load_data()

    pars= list(range(100))
    # show the data distribution among all participants.
    count_all= 0
    for par in pars:
        cifar_class_count = dict()
        for i in range(10):
            cifar_class_count[i] = 0
        count=0
        _, data_iterator = helper.train_data[par]
        for batch_id, batch in enumerate(data_iterator):
            data, targets= batch
            for t in targets:
                cifar_class_count[t.item()]+=1
            count += len(targets)
        count_all+=count
        print(par, cifar_class_count,count,max(zip(cifar_class_count.values(), cifar_class_count.keys())))

    print('avg', count_all*1.0/100)