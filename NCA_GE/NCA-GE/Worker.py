import numpy as np
import scipy.stats as stats
from Graph import *
from Neural_Net import *
from Summary import *
import Constants
import shutil
import time

neural_net = None
full_batch = None

"""
Variables for controlling race conditions
"""
lock = threading.Lock()
graph_train_number 	= 0
graph_test_number	= 0
graph_train		 	= None
graph_test		 	= None
current_mode		= None
kendall_b_tau 		= []

class Worker():
	def __init__(self, id, session, learning_rate, epochs, epochs_test, total_graphs, train_nodes, test_nodes, summary):
		self.tid = id
		self.learning_rate = learning_rate
		self.session = session
		self.increase_epoch = epochs.assign_add(1)
		self.increase_epoch_test = epochs_test.assign_add(1)
		self.total_graphs = total_graphs
		self.increase_graph = total_graphs.assign_add(1)
		self.increase_node = train_nodes.assign_add(1)
		self.increase_node_test = test_nodes.assign_add(1)
		self.total_graphs = total_graphs
		self.summary = summary

		self.graph = Graph()
		self.folder_name = Constants.FLD_GRAPH
		self.train_subfolder = Constants.TRAIN_SUB_FLD
		self.test_subfolder = Constants.TEST_SUB_FLD
		#if Constants.TEST_NUM > 0:
		#	self.test_subfolder = "Test_" + str(Constants.TEST_NUM) + "/"
		self.real_net_subfolder = "Real_Networks/"
		self.test_number = -1
		self.mode = Constants.MODE
		self.load_graph_set_data()
		self.local_batch = []
		self.last_batch = -1

		if self.tid == 'global':
			global neural_net, current_mode
			global batch_index, full_batch
			neural_net = Neural_Net(self.tid, session, learning_rate)
			full_batch = []
			current_mode = Constants.MODE

	#---------------------------------------------------------------------------
	def load_graph_set_data(self):
		self.train_files = [f for f in os.listdir(self.folder_name + self.train_subfolder) if os.path.isfile(self.folder_name + self.train_subfolder + f)]
		self.train_size = len(self.train_files)
		self.test_files = [f for f in os.listdir(self.folder_name + self.test_subfolder) if os.path.isfile(self.folder_name + self.test_subfolder + f)]
		self.test_size = len(self.test_files)
		self.real_net_files = [f for f in os.listdir(self.folder_name + self.real_net_subfolder) if os.path.isfile(self.folder_name + self.real_net_subfolder + f)]
		self.real_net_size = len(self.real_net_files)

	#---------------------------------------------------------------------------
	def work(self, coordinator, saver):
		global kendall_b_tau
		while not coordinator.should_stop():
			sub_folder, file_name, epoch_end, error = self.load_next_graph()
			if not error:
				node_count = self.train_over_graph(saver, sub_folder, file_name)
				if self.mode == Constants.REAL_NET:
					self.write_mean_kendall_b_tau(file_name)
					kendall_b_tau = []
				if self.mode == Constants.TRAIN and node_count > Constants.MAX_STEPS:
					break
				elif self.mode != Constants.TRAIN and epoch_end:
					if self.mode == Constants.TEST:
						self.write_mean_kendall_b_tau(None)
					break

	#---------------------------------------------------------------------------
	def load_next_graph(self):
		global graph_train_number, graph_train
		global graph_test_number, graph_test
		global epoch, epoch_test, current_mode

		lock.acquire()
		epoch_end = False
		
		if current_mode == Constants.TRAIN:
			if graph_train is None:
				graph_train = np.arange(self.train_size)
				np.random.shuffle(graph_train)
				graph_train_number = 0
			number = graph_train_number
			g_number = graph_train[graph_train_number]
			sub_folder = self.train_subfolder 
			file_name = self.train_files[g_number]
			graph_train_number += 1
			if graph_train_number >= self.train_size:
				epoch = self.session.run(self.increase_epoch)
				graph_train = None
		else:
			if graph_test is None:
				if current_mode == Constants.TEST:
					graph_test = np.arange(self.test_size)
					np.random.shuffle(graph_test)
				else:
					graph_test = np.arange(self.real_net_size)
				graph_test_number = 0
			number = graph_test_number
			g_number = graph_test[graph_test_number]
			if current_mode == Constants.TEST:
				file_list = self.test_files
				sub_folder = self.test_subfolder
			else:
				file_list = self.real_net_files
				sub_folder = self.real_net_subfolder
			file_name = file_list[g_number]
			graph_test_number += 1
			if current_mode == Constants.TEST:
				set_size = self.test_size
			else:
				set_size = self.real_net_size
			if graph_test_number >= set_size:
				if Constants.MODE != Constants.TRAIN:
					epoch_end = True
				epoch_test = self.session.run(self.increase_epoch_test)
				graph_test = None 
		lock.release()

		path = self.folder_name + sub_folder + file_name
		error = self.graph.read_file(self.folder_name, sub_folder, file_name)
		if Constants.MODE == Constants.REAL_NET:
			print("Graph ", path, " opened successefully (TOTAL = ", number, ")")
		#print(Constants.BATCH_SIZE)

		return sub_folder, file_name, epoch_end, error

	#---------------------------------------------------------------------------
	def train_over_graph(self, saver, sub_folder, file_name):
		graph_finished = False
		while not graph_finished:
			graph_finished, node_count = self.fill_batch()
			#start = time.time()
			#degree = [val for (node, val) in self.graph.graph.degree()]
			#adj_matx = nx.to_scipy_sparse_matrix(self.graph.graph, format='coo')
			self.process_full_batch(node_count, False)
			#end = time.time()
			#elapsed = end - start
			#print('\n', self.graph.get_num_nodes(), ", ", elapsed, '\n')

		graph_count = self.session.run(self.total_graphs)
		while len(self.local_batch) > 0:
			self.process_full_batch(node_count, True)
		if self.mode == Constants.TRAIN:
			graph_count = self.session.run(self.increase_graph)
		else:
			self.calculate_kendall_tau()
			if self.mode == Constants.REAL_NET:
				self.graph.write_predictions(self.folder_name, sub_folder, file_name)
		self.save_model(saver, graph_count)

		return node_count

	#---------------------------------------------------------------------------
	def fill_batch(self):
		for _ in range(0, Constants.BATCH_SIZE):
			d, f, c, id, graph_finished = self.graph.get_data_next_node()
			self.add_to_local_batch(d, f, c, id)
			if self.mode == Constants.TRAIN:
				total_count = self.session.run(self.increase_node)
			else:
				total_count = self.session.run(self.increase_node_test)
			if graph_finished:
				break

		return graph_finished, total_count

	#---------------------------------------------------------------------------
	def process_full_batch(self, node_count, overide):
		selected_batch = None
		if self.last_batch >= 1 or (self.last_batch >= 0 and overide):
			selected_batch = self.local_batch.pop(0)
			selected_batch.shuffle()
			self.last_batch -= 1

		if selected_batch is not None:
			if self.mode == Constants.TRAIN:
				main_loss, reg_loss, c = neural_net.train_network(self.session, selected_batch)
			else:
				main_loss, reg_loss, c = neural_net.test_network(self.session, selected_batch)
				if Constants.MODE != Constants.TRAIN:
					self.graph.predicted_values(selected_batch, c)
			#print("Main Loss = ",main_loss/selected_batch.size, " --- Reg Loss = ", reg_loss/selected_batch.size, "\t(TID = ", self.tid, ")")
			self.summary.add_info(main_loss, reg_loss)
			self.summary.write(node_count, self.mode)

	#---------------------------------------------------------------------------
	def add_to_local_batch(self, d, f, c, id):
		if self.last_batch == -1 or self.local_batch[self.last_batch].size >= Constants.BATCH_SIZE:
			self.local_batch.append(Batch(self.graph.get_adj_mtx()))
			self.last_batch += 1
		self.local_batch[self.last_batch].add_data(d, f, c, id)

	#---------------------------------------------------------------------------
	def calculate_kendall_tau(self):
		global kendall_b_tau
		rank_x = self.graph.norm_cent
		rank_y = self.graph.predictions
		#tau, p_value = stats.kendalltau(rank_x, rank_y)#kendall
		tau, p_value = stats.pearsonr(rank_x, rank_y) #pearson
		print("correlation:", tau)
		#spearman, p_value = stats.spearmanr(x, y)

		lock.acquire()
		if Constants.MODE == Constants.REAL_NET:
			print("TAU = ", tau)
		kendall_b_tau.append(tau)
		lock.release()

	#---------------------------------------------------------------------------
	def write_mean_kendall_b_tau(self, file_name):
		global kendall_b_tau
		if self.mode == Constants.TEST:
			sub_folder = self.test_subfolder
			name = self.folder_name + "Metadata/" + sub_folder + "Kendall_" + Constants.SUMMARY_NAME + ".dat"
		else:
			sub_folder = self.real_net_subfolder
			folder = self.folder_name + "Metadata/" + sub_folder + 'result/' + Constants.SUMMARY_NAME + "/"
			if not os.path.exists(folder):
				os.makedirs(folder)
			name = folder + "Kendall_" + file_name + ".dat"
		f = open(name, "w")
		f.write(str(np.mean(kendall_b_tau)) + "\t" + str(np.std(kendall_b_tau)) + "\n")
		f.close()

	#---------------------------------------------------------------------------
	def save_model(self, saver, graph_count):
		if graph_count % Constants.SAVER_INTERVAL == 0:
			#print ("Saving model..............")
			if Constants.SAVE_NETWORK == True and Constants.MODE == Constants.TRAIN:
				saver.save(self.session, Constants.MODEL_PATH+'/model.cptk')
			#print ("Model saved!")
