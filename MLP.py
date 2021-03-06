# -*- coding: cp936 -*-
import Config
import random,math
import numpy as np
import time,os
import multiprocessing
import copy_reg,types

"""
#solve the problem that can't use pool.map(self.~)
def _pickle_method(method):
    func_name = method.im_func.__name__
    obj = method.im_self
    cls = method.im_class
    if func_name.startswith('__') and not func_name.endswith('__'): #deal with mangled names
        cls_name = cls.__name__.lstrip('_')
        func_name = '_' + cls_name + func_name
    return _unpickle_method, (func_name, obj, cls)

def _unpickle_method(func_name, obj, cls):
    for cls in cls.__mro__:
        try:
            func = cls.__dict__[func_name]
        except KeyError:
            pass
        else:
            break
    return func.__get__(obj, cls)

copy_reg.pickle(types.MethodType, _pickle_method, _unpickle_method)
"""

class Cost:
    def __init__(self,grad_Eb,grad_W1,grad_b1,grad_W2,loss,correct,dropout_histories):
        self.grad_Eb=grad_Eb
        self.grad_W1=grad_W1
        self.grad_W2=grad_W2
        self.grad_b1=grad_b1
        self.loss=loss
        self.correct=correct
        self.dropout_histories=dropout_histories

class MLP(object):
    def __init__(self,size,Eb,W1,b1,W2,pre_computed_ids=None,features=None,labels=None):#size:[50*48,200,|transitions|]
        self.config=Config.Config()
        self.layer_num=len(size)
        self.size=size
        self.b=[np.random.randn(size[1],1)]
        self.w=[np.random.randn(y,x) for x,y in zip(size[:-1],size[1:])]
        #print type(self.b[0][0][0])
        for i in range(len(b1)):
            self.b[0][i]=b1[i]
        self.w[0]=W1
        self.w[1]=W2
        self.Eb=Eb
        self.Eb[self.config.word_tokens_num:self.config.pos_tokens_up_bound,self.config.pos_emb_size:]*=0
        self.Eb[self.config.pos_tokens_up_bound:,self.config.label_emb_size:]*=0

        self.num_tokens=self.config.num_tokens
        self.hidden_size=size[1]
        self.embed_size=len(Eb[0])
        self.num_labels=size[2]
        self.reg_parameter=self.config.reg_parameter
        self.batch_size=self.config.batch_size #10000
        self.alpha=self.config.alpha
        self.ada_eps=self.config.ada_eps
        self.training_threads=self.config.training_threads
        self.trunk_size=self.batch_size/self.training_threads

        self.grad_w=[np.zeros(w.shape) for w in self.w]
        self.grad_b=[np.zeros(b.shape) for b in self.b]
        self.grad_Eb=np.zeros(self.Eb.shape)

        self.eg2w=[np.zeros(w.shape) for w in self.w]
        self.eg2b=[np.zeros(b.shape) for b in self.b]
        self.eg2Eb=np.zeros(self.Eb.shape)

        if not pre_computed_ids==None:
            self.pre_computed_ids=pre_computed_ids
            self.pre_map={}
            for i in xrange(len(pre_computed_ids)):
                self.pre_map[pre_computed_ids[i]]=i
            self.grad_saved=np.zeros([self.hidden_size,len(self.pre_map)])
        if not features==None:
            self.features=features
        if not labels==None:
            self.labels=labels
        print size

    """
    def __getstate__(self):
            self_dict = self.__dict__.copy()
            del self_dict['pool']
            return self_dict

    def __setstate__(self, state):
            self.__dict__.update(state)
    """
    def set_samples(self,pre_computed_ids,features,labels):
        self.pre_computed_ids=pre_computed_ids
        self.pre_map={}
        for i in xrange(len(pre_computed_ids)):
            self.pre_map[pre_computed_ids[i]]=i
        self.grad_saved=np.zeros([self.hidden_size,len(self.pre_map)])
        self.features=features
        self.labels=labels

    def train(self,iter):
        start=time.time()
        training_data=self.pre_process()
        for i in xrange(iter):
            print "iter ",i
            random.shuffle(training_data)
            batchs=[training_data[j:j+self.batch_size]
                            for j in range(0,len(training_data),self.batch_size)]
            for batch in batchs:
                self.compute_cost_function(batch)
                self.update()
                self.grad_w[0]*=0
                self.grad_w[1]*=0
                self.grad_b[0]*=0
                self.grad_Eb*=0

    def compute_cost_function(self,batch):
        self.costs=[]
        self.loss=0
        self.correct=0
        time1=time.time()
        pre_computed_ids=self.get_pre_computed_ids(batch)
        self.pre_compute(pre_computed_ids)
        time2=time.time()
        print "pre computing used time:",time2-time1
        self.grad_saved*=0
    
        trunks=[batch[j:j+self.trunk_size]
                         for j in xrange(0,len(batch),self.trunk_size)]

        """
        #print len(trunks)
        pool=multiprocessing.Pool(processes=self.config.training_threads)
        results=pool.map(self.backprop,trunks)
        print len(results)
        pool.close()
        pool.join()
        for (cost,grad_saved) in results:
            self.merge_cost(cost)
            self.grad_saved+=grad_saved
        """

        #costs=multiprocessing.Queue(self.training_threads)
        process_pool=[]
        mgr=multiprocessing.Manager()
        #costs=mgr.Queue()
        costs=mgr.dict()
        try:
            process_pool=[multiprocessing.Process(target=self.backprop,args=(trunks[re_id],costs,re_id)) for re_id in range(len(trunks))]
        except:
            print "Error: unable to start thread"
        for process in process_pool:
            process.start()
        for process in process_pool:
            process.join()

        for i in xrange(len(trunks)):
            (cost,grad_saved)=costs[i]
            self.merge_cost(cost)
            self.grad_saved+=grad_saved
        

        """
        costs=multiprocessing.Manager().dict()
        self.backprop(batch,costs,0)
        (cost,grad_saved)=costs[0]
        self.merge_cost(cost)
        self.grad_saved+=grad_saved
        """        

        self.loss/=len(trunks)
        time3=time.time()
        print "backprop used time:",time3-time2
        self.add_l2_regularization()
        print "###loss:",self.loss,"###\n###accuracy:",float(self.correct)/len(batch),"###"
        self.back_prop_saved(pre_computed_ids)
        print "save backprop used time:",time.time()-time3

    #@profile
    def backprop(self,mini_batch,costs,return_id):
        #print self.grad_w[1].shape
        forward_time=0
        bp_time=0
        loss=0.0
        correct=0
        grad_w=[np.zeros(w.shape) for w in self.w]
        grad_b=[np.zeros(b.shape) for b in self.b]
        grad_Eb=np.zeros(self.Eb.shape)
        hidden=np.zeros([self.hidden_size,1])
        hidden3=np.zeros([self.hidden_size,1])
        grad_hidden=np.zeros([self.hidden_size,1])
        grad_hidden3=np.zeros([self.hidden_size,1])
        grad_saved=np.zeros([self.hidden_size,len(self.pre_map)])
        mini_batch_size=len(mini_batch)

        dropout_histories=[]

        for i in xrange(mini_batch_size):
            #score=[]
            hidden*=0
            hidden3*=0
            (label,feature)=mini_batch[i]
            offset=0

            #compute active units
            drop_units=self.dropout()

            if self.config.check:
                dropout_histories.append(drop_units)

            time1=time.time()
            for j in xrange(self.config.word_tokens_num):
                E_index=feature[j]
                index=E_index*self.num_tokens+j
                if index in self.pre_map:
                    hidden[:,0]+=self.saved[:,self.pre_map[index]]
                else:
                    hidden[:,0]+=np.dot(self.w[0][:,offset:offset+self.config.embedding_size],np.transpose(self.Eb[E_index,:]))
                offset+=self.config.embedding_size
            for j in xrange(self.config.word_tokens_num,self.config.pos_tokens_up_bound):
                E_index=feature[j]
                index=E_index*self.num_tokens+j
                if index in self.pre_map:
                    hidden[:,0]+=self.saved[:,self.pre_map[index]]
                else:
                    hidden[:,0]+=np.dot(self.w[0][:,offset:offset+self.config.pos_emb_size],np.transpose(self.Eb[E_index,:self.config.pos_emb_size]))
                offset+=self.config.pos_emb_size
            for j in xrange(self.config.pos_tokens_up_bound,self.config.num_tokens):
                E_index=feature[j]
                index=E_index*self.num_tokens+j
                if index in self.pre_map:
                    hidden[:,0]+=self.saved[:,self.pre_map[index]]
                else:
                    hidden[:,0]+=np.dot(self.w[0][:,offset:offset+self.config.label_emb_size],np.transpose(self.Eb[E_index,:self.config.label_emb_size]))
                offset+=self.config.label_emb_size

            hidden=hidden+self.b[0]
            hidden3=np.power(hidden,3)
            hidden3[drop_units]=0
            hidden[drop_units]=0

            #softmax
            score=np.dot(self.w[1],hidden3)

            """
            opt_label=-1
            for j in xrange(self.num_labels):
                if label[j]>=0:
                    if (score[j][0]>score[opt_label][0] or opt_label<0):
                        opt_label=j
            max_score=score[opt_label][0]
            """

            label_a=np.array(label)
            rows=label_a>=0
            opt_label=np.where(score==np.max(score[rows,:]))[0][0]
            max_score=score[opt_label][0]

            sum1=0
            sum2=0

            """
            for j in xrange(self.num_labels):
                if label[j]>=0:
                    #score[j][0]=math.exp(score[j][0]-max_score)
                    if label[j]==1:
                        sum1+=score[j][0]
                    sum2+=score[j][0]
            """
            
            row_one=label_a==1
            score=np.exp(score-max_score)
            sum1+=score[row_one,0][0]
            sum2+=np.sum(score[rows,:])

            if sum1==0:
                print "opt_label=",opt_label
                print "max_score=",max_score
                print "score:",score
                raw_input("pause")
            time6=time.time()

            forward_time+=time6-time1
            loss+=(np.log(sum2)-np.log(sum1))
            if label[opt_label]==1:
                correct+=1

            #compute gradient
            time7=time.time()
            grad_hidden3*=0

            """
            for i in xrange(self.num_labels):
                if label[i]>=0:#important
                    delta=-(label[i]-score[i][0]/sum2)/mini_batch_size
                    grad_w[1][i,:]+=delta*hidden3[:,0]
                    grad_hidden3[:,0]+=delta*self.w[1][i,:]
            """
           
            z_rows=label_a<0
            #print label_a.shape,score[:,0].shape
            delta=-(label_a-score[:,0]/sum2)/mini_batch_size
            delta[z_rows]=0
            grad_w[1]+=np.outer(delta,hidden3[:,0])
            grad_hidden3[:,0]+=np.dot(delta,self.w[1])
            grad_hidden3[drop_units]=0

            grad_hidden*=0
            grad_hidden=grad_hidden3*3*hidden*hidden
            grad_hidden[drop_units]=0
            grad_b[0]+=grad_hidden


            offset=0
            for j in xrange(self.config.word_tokens_num):
                E_index=feature[j]
                index=E_index*self.num_tokens+j
                if index in self.pre_map:
                    grad_saved[:,self.pre_map[index]]+=grad_hidden[:,0]
                else:
                    grad_w[0][:,offset:offset+self.config.embedding_size]+=np.outer(grad_hidden[:,0],self.Eb[E_index,:])
                    grad_Eb[E_index,:]+=np.dot(np.transpose(grad_hidden[:,0]),self.w[0][:,offset:offset+self.config.embedding_size])
                offset+=self.config.embedding_size
            for j in xrange(self.config.word_tokens_num,self.config.pos_tokens_up_bound):
                E_index=feature[j]
                index=E_index*self.num_tokens+j
                if index in self.pre_map:
                    grad_saved[:,self.pre_map[index]]+=grad_hidden[:,0]
                else:
                    grad_w[0][:,offset:offset+self.config.pos_emb_size]+=np.outer(grad_hidden[:,0],self.Eb[E_index,:self.config.pos_emb_size])
                    grad_Eb[E_index,:self.config.pos_emb_size]+=np.dot(np.transpose(grad_hidden[:,0]),self.w[0][:,offset:offset+self.config.pos_emb_size])
                offset+=self.config.pos_emb_size
            for j in xrange(self.config.pos_tokens_up_bound,self.config.num_tokens):
                E_index=feature[j]
                index=E_index*self.num_tokens+j
                if index in self.pre_map:
                    grad_saved[:,self.pre_map[index]]+=grad_hidden[:,0]
                else:
                    grad_w[0][:,offset:offset+self.config.label_emb_size]+=np.outer(grad_hidden[:,0],self.Eb[E_index,:self.config.label_emb_size])
                    grad_Eb[E_index,:self.config.label_emb_size]+=np.dot(np.transpose(grad_hidden[:,0]),self.w[0][:,offset:offset+self.config.label_emb_size])
                offset+=self.config.label_emb_size

            time9=time.time()
            bp_time=time9-time7

        loss/=len(mini_batch)
        #print grad_w[1]
        
        cost=Cost(grad_Eb,grad_w[0],grad_b[0],grad_w[1],loss,correct,dropout_histories)
        costs[return_id]=(cost,grad_saved)
        #costs.put((cost,grad_saved))
        #return (cost,grad_saved)

    def add_l2_regularization(self):
        self.loss+=self.reg_parameter*np.sum(self.w[0]*self.w[0])/2.0
        self.grad_w[0]+=self.reg_parameter*self.w[0]
        self.loss+=self.reg_parameter*np.sum(self.w[1]*self.w[1])/2.0
        self.grad_w[1]+=self.reg_parameter*self.w[1]
        self.loss+=self.reg_parameter*np.sum(self.b[0]*self.b[0])/2.0
        self.grad_b[0]+=self.reg_parameter*self.b[0]
        self.loss+=self.reg_parameter*np.sum(self.Eb*self.Eb)/2.0
        self.grad_Eb+=self.reg_parameter*self.Eb

    def update(self):
        #print self.grad_w[0]
        self.eg2w[0]+=self.grad_w[0]*self.grad_w[0]
        self.eg2w[1]+=self.grad_w[1]*self.grad_w[1]
        self.eg2b[0]+=self.grad_b[0]*self.grad_b[0]
        self.eg2Eb+=self.grad_Eb*self.grad_Eb

        self.w[0]-=self.alpha*self.grad_w[0]/np.sqrt(self.eg2w[0]+self.ada_eps)
        self.w[1]-=self.alpha*self.grad_w[1]/np.sqrt(self.eg2w[1]+self.ada_eps)
        self.b[0]-=self.alpha*self.grad_b[0]/np.sqrt(self.eg2b[0]+self.ada_eps)
        self.Eb-=self.alpha*self.grad_Eb/np.sqrt(self.eg2Eb+self.ada_eps)

    def pre_process(self):
        training_data=[]
        for i in xrange(len(self.features)):
            training_data.append((self.labels[i],self.features[i]))
        return training_data

    def pre_process_batch(self,batch):
        training_data=[]
        for i in xrange(len(batch)):
            (label,feature)=batch[i]
            offset=0
            x=np.zeros([self.size[0],1])
            for j in xrange(len(feature)):
                for k in xrange(offset,offset+self.embed_size):
                    x[k]=self.Eb[feature[j]][k-offset]
                offset+=self.embed_size
            training_data.append((x,label,feature))
        #print "batch len:",len(training_data)
        training_data=dict.fromkeys(training_data)
        return training_data

    def get_pre_computed_ids(self,batch):
        feature_ids={}
        for i in range(len(batch)):
            (label,feature)=batch[i]
            for j in range(len(feature)):
                tok=feature[j]
                index=tok*self.num_tokens+j
                if ((index in self.pre_map) and (index not in feature_ids)):
                    #feature_ids.append(index)
                    feature_ids[index]=None
        """feature_ids_list=[]
        for i in feature_ids:
            feature_ids_list.append(i)"""
        return feature_ids

    def dropout(self):
        rand=np.random.rand(self.config.hidden_size)
        #active_units=rand>=self.config.dropout_prob
        drop_units=rand<self.config.dropout_prob
        return drop_units
        #print np.sum(np.ones(self.config.hidden_size)[self.active_units])

    def pre_compute(self,candidates):
        self.saved=np.zeros([self.hidden_size,len(self.pre_map)])
        for i in candidates:
            map_x=self.pre_map[i]
            E_index=i/self.num_tokens
            pos=i%self.num_tokens
            if pos<self.config.word_tokens_num:
                offset=pos*self.config.embedding_size
                self.saved[:,map_x]+=np.dot(self.w[0][:,offset:offset+self.config.embedding_size],np.transpose(self.Eb[E_index,:]))
            elif self.config.word_tokens_num-1<pos<self.config.pos_tokens_up_bound:
                offset=self.config.word_tokens_num*self.config.embedding_size+(pos-self.config.word_tokens_num)*self.config.pos_emb_size
                self.saved[:,map_x]+=np.dot(self.w[0][:,offset:offset+self.config.pos_emb_size],np.transpose(self.Eb[E_index,:self.config.pos_emb_size]))
            else:
                offset=self.config.word_tokens_num*self.config.embedding_size+self.config.pos_tokens_num*self.config.pos_emb_size+(pos-self.config.pos_tokens_up_bound)*self.config.label_emb_size
                self.saved[:,map_x]+=np.dot(self.w[0][:,offset:offset+self.config.label_emb_size],np.transpose(self.Eb[E_index,:self.config.label_emb_size]))
        print "pre_computed ",len(candidates)
        

    def back_prop_saved(self,features_seen):
        #print self.grad_saved
        for i in features_seen:
            map_x=self.pre_map[i]
            E_index=i/self.num_tokens
            pos=i%self.num_tokens

            if pos<self.config.word_tokens_num:
                offset=pos*self.config.embedding_size
                self.grad_w[0][:,offset:offset+self.config.embedding_size]+=np.outer(self.grad_saved[:,map_x],self.Eb[E_index,:])
                self.grad_Eb[E_index,:]+=np.dot(np.transpose(self.grad_saved[:,map_x]),self.w[0][:,offset:offset+self.config.embedding_size])
            elif self.config.word_tokens_num-1<pos<self.config.pos_tokens_up_bound:
                offset=self.config.word_tokens_num*self.config.embedding_size+(pos-self.config.word_tokens_num)*self.config.pos_emb_size
                self.grad_w[0][:,offset:offset+self.config.pos_emb_size]+=np.outer(self.grad_saved[:,map_x],self.Eb[E_index,:self.config.pos_emb_size])
                self.grad_Eb[E_index,:self.config.pos_emb_size]+=np.dot(np.transpose(self.grad_saved[:,map_x]),self.w[0][:,offset:offset+self.config.pos_emb_size])
            else:
                offset=self.config.word_tokens_num*self.config.embedding_size+self.config.pos_tokens_num*self.config.pos_emb_size+(pos-self.config.pos_tokens_up_bound)*self.config.label_emb_size
                self.grad_w[0][:,offset:offset+self.config.label_emb_size]+=np.outer(self.grad_saved[:,map_x],self.Eb[E_index,:self.config.label_emb_size])
                self.grad_Eb[E_index,:self.config.label_emb_size]+=np.dot(np.transpose(self.grad_saved[:,map_x]),self.w[0][:,offset:offset+self.config.label_emb_size])
            
    def merge_cost(self,cost):
        self.grad_w[0]+=cost.grad_W1
        self.grad_w[1]+=cost.grad_W2
        self.grad_b[0]+=cost.grad_b1
        self.grad_Eb+=cost.grad_Eb
        self.loss+=cost.loss
        self.correct+=cost.correct
        if self.config.check:
            self.dropout_histories+=cost.dropout_histories

    def check_gradient(self):
        batch=self.pre_process()
        print "---checking gradient---"
        self.dropout_histories=[]
        self.compute_cost_function(batch)
        self.compute_numerical_gradient(batch)
        """
        for i in range(len(self.grad_Eb)):
            print self.num_grad_Eb[i,:]
            print self.grad_Eb[i,:]
            print "------------------------"
        """
        #print np.sum(self.num_grad_w[1]*self.num_grad_w[1])
        #print np.sum(self.grad_w[1]*self.grad_w[1])
        diff_grad_w1=np.sum(np.power(self.num_grad_w[0]-self.grad_w[0],2))/np.sum(np.power(self.num_grad_w[0]+self.grad_w[0],2))
        diff_grad_b1=np.sum(np.power(self.num_grad_b[0]-self.grad_b[0],2))/np.sum(np.power(self.num_grad_b[0]+self.grad_b[0],2))
        diff_grad_w2=np.sum(np.power(self.num_grad_w[1]-self.grad_w[1],2))/np.sum(np.power(self.num_grad_w[1]+self.grad_w[1],2))
        diff_grad_Eb=np.sum(np.power(self.num_grad_Eb-self.grad_Eb,2))/np.sum(np.power(self.num_grad_Eb+self.grad_Eb,2))
        print "diff w1:",diff_grad_w1
        print "diff b1:",diff_grad_b1
        print "diff w2:",diff_grad_w2
        print "diff Eb:",diff_grad_Eb

    def compute_numerical_gradient(self,batch):
        self.num_grad_w=[np.zeros(w.shape) for w in self.w]
        self.num_grad_b=[np.zeros(b.shape) for b in self.b]
        self.num_grad_Eb=np.zeros(self.Eb.shape)
        epsilon=1e-6
        print "checking w1"
        for i in xrange(len(self.w[0])):
            for j in xrange(len(self.w[0][0])):
                self.w[0][i][j]+=epsilon
                p_eps_cost=self.compute_cost(batch)
                self.w[0][i][j]-=2*epsilon
                n_eps_cost=self.compute_cost(batch)
                self.num_grad_w[0][i][j]= (p_eps_cost - n_eps_cost) / (2 * epsilon)
                self.w[0][i][j]+=epsilon
        print "checking b1"
        for i in xrange(len(self.b[0])):
            self.b[0][i]+=epsilon
            p_eps_cost=self.compute_cost(batch)
            self.b[0][i]-=2*epsilon
            n_eps_cost=self.compute_cost(batch)
            self.num_grad_b[0][i]= (p_eps_cost - n_eps_cost) / (2 * epsilon)
            self.b[0][i]+=epsilon
        print "checking w2"
        for i in xrange(len(self.w[1])):
            for j in xrange(len(self.w[1][0])):
                self.w[1][i][j]+=epsilon
                p_eps_cost=self.compute_cost(batch)
                self.w[1][i][j]-=2*epsilon
                n_eps_cost=self.compute_cost(batch)
                self.num_grad_w[1][i][j]= (p_eps_cost - n_eps_cost) / (2 * epsilon)
                self.w[1][i][j]+=epsilon
        print "checking Eb"
        for i in xrange(len(self.Eb)):
            for j in xrange(len(self.Eb[0])):
                self.Eb[i][j]+=epsilon
                p_eps_cost=self.compute_cost(batch)
                self.Eb[i][j]-=2*epsilon
                n_eps_cost=self.compute_cost(batch)
                self.num_grad_Eb[i][j]= (p_eps_cost - n_eps_cost) / (2 * epsilon)
                self.Eb[i][j]+=epsilon

    def compute_cost(self,batch):
        v_cost=0
        hidden=np.zeros([self.hidden_size,1])
        hidden3=np.zeros([self.config.hidden_size,1])
        for i in range(len(batch)):
            drop_units = self.dropout_histories[i]
            hidden*=0
            (label,feature)=batch[i]
            offset=0
            for j in range(self.num_tokens):
                E_index=feature[j]
                if j<self.config.word_tokens_num:
                    hidden[:,0]+=np.dot(self.w[0][:,offset:offset+self.config.embedding_size],np.transpose(self.Eb[E_index,:]))
                    offset+=self.config.embedding_size
                elif self.config.word_tokens_num-1<j<self.config.pos_tokens_up_bound:
                    hidden[:,0]+=np.dot(self.w[0][:,offset:offset+self.config.pos_emb_size],np.transpose(self.Eb[E_index,:self.config.pos_emb_size]))
                    offset+=self.config.pos_emb_size
                elif j>=self.config.pos_tokens_up_bound:
                    hidden[:,0]+=np.dot(self.w[0][:,offset:offset+self.config.label_emb_size],np.transpose(self.Eb[E_index,:self.config.label_emb_size]))
                    offset+=self.config.label_emb_size
            hidden+=self.b[0]
            #hidden1=np.dot(self.w[0],x)+self.b[0]
            hidden3=np.power(hidden,3)
            hidden3[drop_units]=0
            score=np.dot(self.w[1],hidden3)

            opt_label=-1
            for j in range(self.num_labels):
                if label[j]>=0:
                    if (opt_label<0 or score[j][0]>score[opt_label][0]):
                        opt_label=j

            max_score=score[opt_label][0]
            sum1=0
            sum2=0
            for j in range(self.num_labels):
                if label[j]>=0:
                    score[j][0]=np.exp(score[j][0]-max_score)
                    if label[j]==1:
                        sum1+=score[j][0]
                    sum2+=score[j][0]
            if sum1==0:
                print "opt_label=",opt_label
                print "max_score=",max_score
                print "score:",score
                raw_input("pause")

            v_cost+=np.log(sum2)-np.log(sum1)
        v_cost/=len(batch)
        v_cost+=self.reg_parameter*np.sum(self.w[0]*self.w[0])/2.0
        v_cost+=self.reg_parameter*np.sum(self.w[1]*self.w[1])/2.0
        v_cost+=self.reg_parameter*np.sum(self.b[0]*self.b[0])/2.0
        v_cost+=self.reg_parameter*np.sum(self.Eb*self.Eb)/2.0
        return v_cost

    def get_w1(self):
        return self.w[0]

    def get_b1(self):
        return self.b[0]

    def get_w2(self):
        return self.w[1]

    def get_Eb(self):
        return self.Eb

    def compute_scores(self,features):
        hidden=np.zeros([self.hidden_size,1])
        offset=0
        for j in range(len(features)):
                E_index=features[j]
                if j<self.config.word_tokens_num:
                    hidden[:,0]+=np.dot(self.w[0][:,offset:offset+self.config.embedding_size],np.transpose(self.Eb[E_index,:]))
                    offset+=self.config.embedding_size
                elif self.config.word_tokens_num-1<j<self.config.pos_tokens_up_bound:
                    hidden[:,0]+=np.dot(self.w[0][:,offset:offset+self.config.pos_emb_size],np.transpose(self.Eb[E_index,:self.config.pos_emb_size]))
                    offset+=self.config.pos_emb_size
                elif j>=self.config.pos_tokens_up_bound:
                    hidden[:,0]+=np.dot(self.w[0][:,offset:offset+self.config.label_emb_size],np.transpose(self.Eb[E_index,:self.config.label_emb_size]))
                    offset+=self.config.label_emb_size
        hidden+=self.b[0]
        hidden=hidden*hidden*hidden
        scores=np.dot(self.w[1],hidden)
        return scores

